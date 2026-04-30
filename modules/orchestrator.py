"""
╔══════════════════════════════════════════════════════╗
║                 Orchestrator 模块                    ║
║                                                      ║
║  多 Agent 编排器                                     ║
║                                                      ║
║  职责：                                              ║
║    1. 任务分解：将复杂任务拆成 2 个子任务             ║
║    2. SubAgent 调度：为每个子任务启动独立 Agent       ║
║    3. 结果聚合：将所有子任务结果格式化给主 Agent       ║
║                                                      ║
║  与 Workflow 的区别：                                ║
║    Workflow     → 固定步骤，用已有工具，无 LLM 参与  ║
║    Orchestrator → 动态分解，调用 SubAgent（LLM）     ║
║                                                      ║
║  调用链：                                            ║
║    decompose_task() → Claude API（任务分解）          ║
║    run_subagent()   × N → Claude API（子任务执行）   ║
║                                                      ║
║  orchestrate_stream() 是流式版本，逐步 yield 每个    ║
║  LLM 调用的开始和结果，让 Pipeline 实时展示           ║
╚══════════════════════════════════════════════════════╝
"""

import asyncio
import re
from typing import AsyncGenerator

import anthropic
import httpx

from .subagent import run_subagent, SUBAGENT_SYSTEM

_DECOMPOSE_SYSTEM = (
    '你是任务分解专家。将用户的复杂任务拆解为 2 个独立、具体、可执行的子任务。'
    '每行输出一个子任务，格式：数字. 子任务描述。不要解释，直接输出列表。'
)


async def decompose_task(task: str, api_key: str) -> list[str]:
    """调用 Claude 将复杂任务分解为子任务列表。失败时返回空列表。"""
    try:
        client = anthropic.AsyncAnthropic(
            api_key=api_key,
            http_client=httpx.AsyncClient(verify=False, timeout=15),
        )
        resp = await client.messages.create(
            model      = 'claude-haiku-4-5-20251001',
            max_tokens = 256,
            system     = _DECOMPOSE_SYSTEM,
            messages   = [{'role': 'user', 'content': task}],
        )
        text  = next((b.text for b in resp.content if b.type == 'text'), '')
        lines = [l.strip() for l in text.split('\n') if l.strip() and l[0].isdigit()]
        tasks = [re.sub(r'^\d+[\.。]\s*', '', l) for l in lines]
        return tasks[:2]
    except Exception:
        return []


async def _run_indexed(subtask: str, api_key: str, index: int) -> tuple[int, str, dict]:
    """执行单个 SubAgent，返回 (index, task, result)"""
    result = await run_subagent(subtask, api_key)
    return index, subtask, result


async def orchestrate_stream(task: str, api_key: str) -> AsyncGenerator[dict, None]:
    """
    流式编排器 — 逐步 yield 每个阶段的事件，让 Pipeline 实时展示每次 LLM 调用。

    事件类型：
      decompose_start  — 开始调用 LLM 分解任务
      decompose_done   — 分解完成，含子任务列表
      subagent_start   — 某个 SubAgent 开始执行
      subagent_done    — 某个 SubAgent 完成
      complete         — 全部完成，含聚合 context_text
      error            — 任意步骤失败
    """
    # ── Step 1: 分解任务（LLM 调用 #1）─────────────
    yield {'event': 'decompose_start', 'task': task,
           'system_prompt': _DECOMPOSE_SYSTEM}

    subtasks = await decompose_task(task, api_key)

    if not subtasks:
        yield {'event': 'error', 'message': '任务分解失败（API Key 无效或网络问题）'}
        return

    yield {'event': 'decompose_done', 'subtasks': subtasks,
           'count': len(subtasks)}

    # ── Step 2: 顺序执行 SubAgent ────────────────────────────
    results: list[dict | None] = [None] * len(subtasks)
    for i, st in enumerate(subtasks):
        yield {'event': 'subagent_start', 'index': i + 1, 'task': st,
               'total': len(subtasks), 'system_prompt': SUBAGENT_SYSTEM}
        result = await run_subagent(st, api_key)
        results[i] = result
        yield {'event': 'subagent_done', 'index': i + 1, 'task': st,
               'result': result, 'completed': i + 1, 'total': len(subtasks)}

    # ── Step 3: 聚合 context_text ─────────────────────────
    lines = ['[Orchestrator 子任务预分析]']
    for i, (st, r) in enumerate(zip(subtasks, results), 1):
        content = (r.get('result', r.get('error', '')) if r and r['success']
                   else (r or {}).get('error', '执行失败'))
        lines.append(f'子任务 {i}（{st}）：{content[:200]}')

    yield {
        'event':        'complete',
        'success':      True,
        'subtasks':     len(subtasks),
        'results':      results,
        'context_text': '\n'.join(lines),
    }
