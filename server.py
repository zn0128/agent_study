"""
╔══════════════════════════════════════════════════════╗
║                   server.py                          ║
║                                                      ║
║  AI Agent 演示服务器                                 ║
║                                                      ║
║  本文件只做两件事：                                   ║
║    1. FastAPI HTTP 服务（接收请求、SSE 推送）         ║
║    2. Pipeline 串联（按顺序调用各模块）               ║
║                                                      ║
║  所有模块均为真实实现（非视觉模拟）：                 ║
║    memory.py       三层记忆体系                      ║
║    memory_bank.py  知识文档库                        ║
║    rag.py          知识库检索                        ║
║    news.py         实时新闻抓取                      ║
║    guardrails.py   安全护栏                          ║
║    tools.py        工具定义与执行                    ║
║    planning.py     任务规划                          ║
║    prompt.py       提示词构建                        ║
║    evaluation.py   质量评估                          ║
║    mcp_server.py   MCP 协议服务器                    ║
║    skills.py       工具结果后处理技能                ║
║    hitl.py         人工审核队列                      ║
║    subagent.py     独立 SubAgent 调用                ║
║    workflow.py     预定义工作流引擎                   ║
║    orchestrator.py 多 Agent 编排                     ║
╚══════════════════════════════════════════════════════╝
"""

import asyncio
import json
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

from typing import AsyncGenerator

import anthropic
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from modules.memory       import (VectorMemory, load_persisted, save_persisted,
                                   get_session, retrieve_all)
from modules.guardrails   import check_input, check_output
from modules.tools        import TOOL_DEFINITIONS, execute_tool
from modules.planning     import analyze_task, classify_task
from modules.prompt       import build_system_prompt
from modules.evaluation   import build_eval_report
from modules.memory_bank  import MemoryBank
from modules.mcp_server   import MCPServer
from modules.skills       import activate as skills_activate, list_skills
from modules.hitl         import HITLQueue
from modules.workflow     import WorkflowEngine
from modules.orchestrator import orchestrate_stream

# ─────────────────────────────────────────────
#  初始化
# ─────────────────────────────────────────────
app = FastAPI()

persisted_mem: list[dict] = load_persisted()
vector_mem = VectorMemory()
vector_mem.rebuild(persisted_mem)
print(f'[Memory]     已加载 {len(persisted_mem)} 条持久化记忆')

memory_bank = MemoryBank()
print(f'[MemoryBank] 已加载 {memory_bank.size()} 篇文档')

def add_persisted_memory(entry: dict):
    persisted_mem.append(entry)
    vector_mem.add(entry)
    save_persisted(persisted_mem)

# MCP Server：注册所有工具的元数据
mcp_server = MCPServer('agent-tools-server', '1.0.0')
for _tool in TOOL_DEFINITIONS:
    mcp_server.register(
        name        = _tool['name'],
        description = _tool['description'],
        input_schema= _tool['input_schema'],
        handler     = None,   # pipeline 用 executor lambda；HTTP API 也用 executor
    )
print(f'[MCP]        已注册 {mcp_server.tool_count()} 个工具')

# HITL Queue
hitl_queue = HITLQueue(auto_approve_timeout=60.0)

# Workflow Engine
workflow_engine = WorkflowEngine()

print('[Skills]     已注册', len(list_skills()), '个技能')


# ─────────────────────────────────────────────
#  辅助函数：Pipeline log 格式化
# ─────────────────────────────────────────────
def _skill_log(skill_name: str, result: dict) -> str:
    if skill_name == 'knowledge_integration':
        summary = result.get('_skill_summary', '')
        return f'整合 {result.get("_skill_topic_count", 0)} 条 → {summary[:80]}'
    if skill_name == 'news_digest':
        digest = result.get('_skill_digest', '').replace('\n', ' | ')
        return f'摘要({result.get("_skill_count",0)}条) → {digest[:80]}'
    if skill_name == 'calculation_context':
        ctx = result.get('_skill_context', '')
        return f'量级：{ctx}' if ctx else '无需量级说明'
    return f'{skill_name} 完成'


def _fmt_memories(facts: list, hits: list) -> str:
    """将记忆条目格式化为可读字符串"""
    parts = []
    for f in facts[:3]:
        parts.append(f'[{f.get("category","?")}] {f.get("content","")[:30]}')
    for h in hits[:3]:
        parts.append(f'[{h.get("category","?")}] {h.get("content","")[:30]} ({h.get("score",0):.2f})')
    return ' | '.join(parts) if parts else '（无）'


def _fmt_tool_result(name: str, result: dict) -> str:
    """将工具执行结果格式化为一行可读摘要"""
    if 'error' in result:
        return f'错误: {result["error"][:50]}'
    if name == 'calculate':
        return f'{result.get("expression","")} = {result.get("result","")}'
    if name == 'get_datetime':
        return f'{result.get("date","")} {result.get("time","")}'
    if name == 'search_knowledge_base':
        if result.get('found') and result.get('results'):
            topics = ' | '.join(r['topic'] for r in result['results'])
            return f'命中 {result["count"]} 条 → {topics}'
        return '未找到相关知识'
    if name == 'get_news':
        if result.get('news'):
            src   = result.get('source', '')[:20]
            title = result['news'][0]['title'][:40]
            return f'{src}: "{title}"…'
        return '新闻源不可达'
    if name == 'save_to_memory':
        if result.get('success'):
            return f'已存入 [{result.get("saved","")[:40]}] → memories.json'
        return str(result)[:60]
    if name == 'add_to_memory_bank':
        if result.get('success'):
            return f'已存入 [{result.get("title","")[:30]}] → memory_bank.json（共 {result.get("bank_size",0)} 篇）'
        return str(result)[:60]
    if name == 'search_memory_bank':
        if result.get('found') and result.get('results'):
            titles = ' | '.join(r['title'][:20] for r in result['results'])
            return f'命中 {result["count"]} 篇 → {titles}'
        return '无匹配文档'
    return str(result)[:80]


def _fmt_context_history(history: list) -> str:
    """显示最近一轮对话的摘要"""
    if len(history) >= 2:
        last_user = history[-2].get('content', '')[:30]
        last_bot  = history[-1].get('content', '')[:30]
        return f'上轮: "{last_user}…" → "{last_bot}…"'
    return '首次对话'


def _fmt_workflow_result(wf_result: dict) -> str:
    """将工作流结果格式化为一行"""
    r = wf_result.get('results', {})
    parts = []
    topics = r.get('extract_topics', {}).get('topics', [])
    if topics:
        parts.append(f'话题: {", ".join(topics[:3])}')
    hits = r.get('search_knowledge', {}).get('hits', [])
    if hits:
        parts.append(f'知识: {" | ".join(h.get("topic","")[:10] for h in hits[:2])}')
    news = r.get('check_news', {})
    if news.get('news'):
        parts.append(f'新闻: "{news["news"][0]["title"][:25]}…"')
    elif news.get('skipped'):
        parts.append('新闻: 跳过（无新闻意图）')
    return ' → '.join(parts) if parts else '无有效输出'


# ─────────────────────────────────────────────
#  API 路由
# ─────────────────────────────────────────────

@app.get('/api/memories')
def get_memories():
    return {'count': len(persisted_mem), 'memories': persisted_mem[-50:]}

@app.get('/api/memory-bank')
def get_memory_bank():
    return {'count': memory_bank.size(), 'documents': memory_bank.list_all()}

@app.get('/api/skills')
def get_skills():
    return {'skills': list_skills()}

# ── MCP 协议端点 ───────────────────────────────

@app.get('/mcp')
def mcp_info():
    return {'server': mcp_server.name, 'version': mcp_server.version,
            'tools': mcp_server.tool_count(),
            'call_history': mcp_server.call_history(10)}

@app.get('/mcp/tools')
def mcp_list_tools():
    return mcp_server.list_tools()

@app.post('/mcp/tools/call')
async def mcp_call_tool(request: Request):
    body = await request.json()
    name = body.get('name')
    args = body.get('arguments', {})
    if not name:
        return JSONResponse({'error': 'missing name'}, status_code=400)
    dummy_session = {'session_facts': [], 'history': []}
    result = mcp_server.call_tool(
        name, args,
        executor=lambda n, a: execute_tool(n, a, dummy_session, add_persisted_memory, memory_bank),
    )
    return result

# ── HITL 端点 ──────────────────────────────────

@app.get('/api/hitl/pending')
def hitl_pending():
    items = hitl_queue.get_pending()
    return {'count': len(items), 'pending': items}

@app.post('/api/hitl/{approval_id}/approve')
def hitl_approve(approval_id: str):
    success = hitl_queue.approve(approval_id)
    return {'success': success, 'id': approval_id, 'action': 'approved'}

@app.post('/api/hitl/{approval_id}/reject')
def hitl_reject(approval_id: str):
    success = hitl_queue.reject(approval_id)
    return {'success': success, 'id': approval_id, 'action': 'rejected'}

# ── 主对话端点 ──────────────────────────────────

@app.post('/api/chat')
async def chat(request: Request):
    body = await request.json()
    message    = body.get('message')
    session_id = body.get('sessionId', 'default')
    api_key    = body.get('apiKey')

    if not message or not api_key:
        return JSONResponse({'error': '缺少 message 或 apiKey'}, status_code=400)

    return StreamingResponse(
        run_pipeline(message, session_id, api_key),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'},
    )


# ─────────────────────────────────────────────
#  Pipeline（全部真实实现）
# ─────────────────────────────────────────────
async def run_pipeline(message: str, session_id: str, api_key: str) -> AsyncGenerator[str, None]:
    session = get_session(session_id)

    def sse(event: str, data: dict) -> str:
        return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'

    def mod(id: str, status: str, log: str, detail: dict = None) -> str:
        d = {'id': id, 'status': status, 'log': log}
        if detail:
            d['detail'] = detail
        return sse('module', d)

    def _ch(h: dict) -> dict:
        """清除 VectorMemory 内部字段"""
        return {k: v for k, v in h.items() if k not in ('tokens', 'tf', 'vec')}

    # 用于 MCP executor：捕获当前请求的 session 上下文
    def _executor(name: str, args: dict):
        return execute_tool(name, args, session, add_persisted_memory, memory_bank)

    try:
        yield mod('langchain', 'active',
                  '框架初始化：memory / memory_bank / rag / news / guardrails / '
                  'tools / planning / prompt / evaluation / mcp / skills / hitl / '
                  'subagent / workflow / orchestrator')
        yield mod('agent', 'active', f'主 Agent 启动 | session={session_id} | 输入: "{message[:50]}{"…" if len(message)>50 else ""}"')


        # ════════════════════════════════════════════
        #  Step 1 · Guardrails（输入护栏）
        # ════════════════════════════════════════════
        yield mod('guardrails', 'processing',
                  f'扫描 {len(message)} 字符 | 检查规则: 破坏性操作 / 提示词注入 / 角色覆盖…')
        await asyncio.sleep(0.10)

        input_safety = check_input(message)
        if not input_safety['safe']:
            yield mod('guardrails', 'blocked',
                      f'拦截 | 规则命中: {input_safety["reason"]} | 消息已丢弃，不发送给 LLM')
            yield sse('text', {'text': f'⚠️ 请求已被安全护栏拦截：{input_safety["reason"]}'})
            yield sse('done', {})
            return
        yield mod('guardrails', 'done', f'通过 | 规则扫描完毕，无危险模式 | 消息长度: {len(message)} 字符')


        # ════════════════════════════════════════════
        #  Step 2 · Memory（三层记忆检索）
        # ════════════════════════════════════════════
        # Layer 1
        sf_preview = ' | '.join(f'[{f.get("category","?")}] {f.get("content","")[:25]}' for f in session["session_facts"]) or '空'
        yield mod('memory', 'processing',
                  f'[Layer 1] 会话内记忆: {len(session["session_facts"])} 条 → {sf_preview}')
        await asyncio.sleep(0.08)

        # Layer 2+3
        yield mod('memory', 'processing',
                  f'[Layer 2+3] TF-IDF 向量检索: 库中 {vector_mem.size()} 条持久化记忆，计算余弦相似度…')
        await asyncio.sleep(0.20)

        mem_result    = retrieve_all(message, session, vector_mem)
        session_facts = mem_result['session_facts']
        vector_hits   = mem_result['vector_hits']

        inject_preview = _fmt_memories(session_facts, vector_hits)
        yield mod('memory', 'processing',
                  f'[注入] 会话内 {len(session_facts)} 条 + 向量命中 {len(vector_hits)} 条 → {inject_preview}')
        await asyncio.sleep(0.08)

        if vector_hits:
            mem_log = (f'命中 {len(vector_hits)} 条 | 最高相关度 {vector_hits[0]["score"]:.3f} | '
                       + _fmt_memories([], vector_hits))
        elif session_facts:
            mem_log = f'会话内 {len(session_facts)} 条 | {_fmt_memories(session_facts, [])} | 持久化库无语义命中'
        else:
            mem_log = f'无相关记忆 | 持久化库 {len(persisted_mem)} 条 | Memory Bank {memory_bank.size()} 篇'
        yield mod('memory', 'done', mem_log, detail={
            'layer1_session_facts': session_facts,
            'layer3_vector_hits':   [_ch(h) for h in vector_hits],
            'persisted_total':      len(persisted_mem),
            'memory_bank_total':    memory_bank.size(),
            'persisted_preview':    [_ch(m) for m in persisted_mem[-5:]],
        })


        # ════════════════════════════════════════════
        #  Step 3 · Prompt（构建系统提示词）
        # ════════════════════════════════════════════
        yield mod('prompt', 'processing', '构建系统提示词（角色定义 + 工具规则 + 记忆注入）…')
        sys_prompt = build_system_prompt(session_facts, vector_hits)
        await asyncio.sleep(0.06)
        mem_injected = len(session_facts) + len(vector_hits)
        mem_detail   = _fmt_memories(session_facts, vector_hits) if mem_injected else '无注入'
        yield mod('prompt', 'done',
                  f'{len(sys_prompt)} 字符 | 工具规则 7 条 | 注入记忆 {mem_injected} 条: {mem_detail}',
                  detail={'system_prompt': sys_prompt, 'length': len(sys_prompt),
                          'injected_memories': session_facts + [_ch(h) for h in vector_hits]})


        # ════════════════════════════════════════════
        #  Step 4 · Context（构建上下文窗口）
        # ════════════════════════════════════════════
        yield mod('context', 'processing', '统计 token 占用，组装 messages 数组…')
        await asyncio.sleep(0.06)
        hist_len  = len(session['history'])
        est_token = hist_len * 120 + len(message) + len(sys_prompt)
        est_pct   = min(round(est_token / 200000 * 100), 99)
        hist_info = _fmt_context_history(session['history'])
        yield mod('context', 'done',
                  f'~{est_token} tokens ({est_pct}% of 200k) | {hist_len//2} 轮历史 | {hist_info}',
                  detail={
                      'estimated_tokens':   est_token,
                      'context_window_pct': f'{est_pct}%',
                      'history_turns':      hist_len // 2,
                      'current_message':    message,
                      'recent_history':     [{'role': m['role'], 'content': m['content'][:150]}
                                             for m in session['history'][-6:]],
                  })


        # ════════════════════════════════════════════
        #  Step 5 · Planning（任务规划）
        # ════════════════════════════════════════════
        yield mod('planning', 'processing',
                  f'LLM 分类中: "{message[:40]}{"…" if len(message)>40 else ""}" | 调用 claude-haiku 判断任务复杂度…')
        plan       = await classify_task(message, api_key)
        is_complex = plan['is_complex']
        method     = plan.get('method', 'llm')
        method_tag = 'LLM 分类' if method == 'llm' else f'关键词兜底（{plan.get("error","")[:40]}）'
        tokens_tag = f' | {plan.get("tokens",0)} tokens' if method == 'llm' else ''
        yield mod('planning', 'done',
                  f'{plan["mode_desc"]} | 方法: {method_tag}{tokens_tag}',
                  detail={'method':         method,
                          'is_complex':     is_complex,
                          'mode':           plan['mode'],
                          'llm_output':     plan.get('llm_output', ''),
                          'tokens':         plan.get('tokens'),
                          'system_prompt':  plan.get('system_prompt', ''),
                          'input_message':  plan.get('input_message', message),
                          'error':          plan.get('error')})

        # 构建初始消息列表（复杂任务可能被 Workflow/Orchestrator 扩充）
        api_msgs = [*session['history'][-8:], {'role': 'user', 'content': message}]


        # ════════════════════════════════════════════
        #  Step 6 · Workflow（复杂任务本地预检索）
        #
        #  Workflow 是纯本地操作（无 LLM），提前检索知识和新闻
        #  作为背景上下文注入给主 LLM。
        #
        #  Orchestrator/SubAgent 不再在这里预跑——
        #  改为工具 orchestrate_task，由主 LLM 在推理时自主决定调用。
        # ════════════════════════════════════════════
        if is_complex:
            yield mod('workflow', 'processing',
                      f'步骤: extract_topics → search_knowledge → check_news | 消息: "{message[:40]}"')
            wf_result  = await workflow_engine.run('complex_analysis', {'message': message})
            wf_context = workflow_engine.format_for_context(wf_result)
            wf_r = wf_result.get('results', {})
            yield mod('workflow', 'done', _fmt_workflow_result(wf_result), detail={
                'steps':            wf_result.get('steps'),
                'extracted_topics': wf_r.get('extract_topics', {}).get('topics', []),
                'knowledge_hits':   [{'topic': h.get('topic'), 'content': h.get('content', '')[:120]}
                                     for h in wf_r.get('search_knowledge', {}).get('hits', [])],
                'news':             [{'title': n['title']} for n in wf_r.get('check_news', {}).get('news', [])]
                                    if wf_r.get('check_news', {}).get('news') else 'skipped',
                'context_injected': wf_context[:500] if wf_context else None,
            })
            if wf_context:
                enriched = message + '\n\n[背景知识预检索结果]\n' + wf_context[:400]
                api_msgs = [*session['history'][-8:], {'role': 'user', 'content': enriched}]


        # ════════════════════════════════════════════
        #  Step 7 · LLM + 工具调用循环（ReAct 核心）
        #
        #  主 LLM 推理 → 自主决定调用哪些工具
        #  orchestrate_task 是其中一个工具选项，
        #  LLM 根据任务性质自行决定是否调用
        # ════════════════════════════════════════════
        msg_preview = api_msgs[-1]['content'][:60].replace('\n', ' ')
        _max_tok    = 2048 if is_complex else 1024
        yield mod('llm', 'processing',
                  f'发送给 Claude | model: claude-haiku-4-5 | max_tokens: {_max_tok} | '
                  f'messages: {len(api_msgs)} 条 | 最新: "{msg_preview}…"',
                  detail={
                      'model':         'claude-haiku-4-5-20251001',
                      'max_tokens':    _max_tok,
                      'system_prompt': sys_prompt,
                      'messages':      [{'role': m['role'],
                                         'content': m['content'][:300] if isinstance(m['content'], str)
                                                    else f'[{type(m["content"]).__name__}]'}
                                        for m in api_msgs],
                  })

        client     = anthropic.AsyncAnthropic(
            api_key=api_key,
            http_client=httpx.AsyncClient(verify=False),
        )
        cursor     = api_msgs
        final_text = ''
        tools_used: list[str] = []

        # 复杂任务移除存储工具，防止模型把报告塞进 save_to_memory JSON 参数
        # orchestrate_task 由 LLM 自主决定调用，不再需要过滤存储工具
        active_tools = TOOL_DEFINITIONS

        for iter_i in range(5):
            resp = await client.messages.create(
                model      = 'claude-haiku-4-5-20251001',
                max_tokens = 2048 if is_complex else 1024,
                system     = sys_prompt,
                tools      = active_tools,
                messages    = cursor,
            )

            if resp.stop_reason in ('end_turn', 'max_tokens'):
                # 收集所有 text 块（max_tokens 截断时可能有多个）
                final_text = '\n\n'.join(b.text for b in resp.content if b.type == 'text')
                trunc = ' [已达输出上限]' if resp.stop_reason == 'max_tokens' else ''
                yield mod('llm', 'done',
                          f'{resp.stop_reason} | in={resp.usage.input_tokens} out={resp.usage.output_tokens} tokens{trunc}'
                          f'| 回答预览: "{final_text[:60]}…"',
                          detail={'stop_reason': resp.stop_reason, 'model': 'claude-haiku-4-5-20251001',
                                  'input_tokens': resp.usage.input_tokens,
                                  'output_tokens': resp.usage.output_tokens,
                                  'full_response': final_text})
                break

            if resp.stop_reason == 'tool_use':
                tool_blocks = [b for b in resp.content if b.type == 'tool_use']
                tools_str   = ', '.join(f'{b.name}({json.dumps(b.input, ensure_ascii=False)[:30]})' for b in tool_blocks)
                yield mod('llm', 'done',
                          f'tool_use | in={resp.usage.input_tokens} tokens | 决策: {tools_str}',
                          detail={'stop_reason': 'tool_use', 'model': 'claude-haiku-4-5-20251001',
                                  'input_tokens': resp.usage.input_tokens,
                                  'tools_called': [{'name': b.name, 'input': b.input, 'id': b.id}
                                                   for b in tool_blocks]})

                tool_results = []

                for tb in tool_blocks:

                    # ── orchestrate_task：异步流式处理，单独路径 ──
                    if tb.name == 'orchestrate_task':
                        task_input = tb.input.get('task', message)
                        yield mod('function_call', 'done',
                                  f'主 LLM 决定调用 orchestrate_task | 任务: "{task_input[:60]}"')
                        yield mod('orchestrator', 'processing',
                                  f'主 LLM 委派任务 → 开始分解 + 调度 SubAgent | "{task_input[:50]}"')

                        orch_context = ''
                        async for ev in orchestrate_stream(task_input, api_key):
                            if ev['event'] == 'decompose_start':
                                yield mod('llm', 'processing',
                                          f'[SubAgent LLM #1] 任务分解 | "{task_input[:50]}"',
                                          detail={
                                              'purpose':       'task_decomposition',
                                              'model':         'claude-haiku-4-5-20251001',
                                              'system_prompt': ev.get('system_prompt', ''),
                                              'messages':      [{'role': 'user', 'content': task_input}],
                                          })

                            elif ev['event'] == 'decompose_done':
                                subtasks_list = ev['subtasks']
                                yield mod('llm', 'done',
                                          f'[SubAgent LLM #1] 分解完成 | '
                                          + ' / '.join(st[:30] for st in subtasks_list),
                                          detail={'subtasks': subtasks_list, 'llm_output': '\n'.join(subtasks_list)})
                                yield mod('orchestrator', 'processing',
                                          f'启动 {len(subtasks_list)} 个 SubAgent…')
                                yield mod('subagent', 'processing',
                                          ' | '.join(st[:25] for st in subtasks_list))

                            elif ev['event'] == 'subagent_start':
                                idx, st = ev['index'], ev['task']
                                yield mod('llm', 'processing',
                                          f'[SubAgent LLM #{idx+1}] {st[:60]}',
                                          detail={
                                              'purpose':       f'subagent_{idx}',
                                              'model':         'claude-haiku-4-5-20251001',
                                              'system_prompt': ev.get('system_prompt', ''),
                                              'messages':      [{'role': 'user', 'content': st}],
                                          })

                            elif ev['event'] == 'subagent_done':
                                idx, r, st = ev['index'], ev['result'], ev['task']
                                tokens = r.get('tokens', '?') if r else '?'
                                preview = r.get('result', r.get('error', ''))[:60] if r else '超时'
                                yield mod('llm', 'done',
                                          f'[SubAgent LLM #{idx+1}] {st[:40]} | {tokens} tokens | {preview}',
                                          detail={'task': st, 'tokens': tokens,
                                                  'result': r.get('result', r.get('error',''))[:500] if r else ''})
                                yield mod('subagent', 'processing',
                                          f'{ev["completed"]}/{ev["total"]} 完成')

                            elif ev['event'] == 'complete':
                                orch_context = ev.get('context_text', '')
                                n = ev['subtasks']
                                yield mod('subagent', 'done', f'全部 {n} 个子任务完成',
                                          detail={'results': [
                                              {'task': r['task'] if r else '', 'tokens': r.get('tokens') if r else None,
                                               'result': r.get('result', r.get('error',''))[:400] if r else ''}
                                              for r in ev['results']]})
                                yield mod('orchestrator', 'done',
                                          f'编排完成，结果回传主 LLM | {n} 个子任务',
                                          detail={'context_text': orch_context[:600]})

                            elif ev['event'] == 'error':
                                yield mod('subagent',    'done', f'跳过 | {ev["message"]}')
                                yield mod('orchestrator','done', f'编排失败: {ev["message"]}')

                        tools_used.append('orchestrate_task')
                        tool_results.append({
                            'type':        'tool_result',
                            'tool_use_id': tb.id,
                            'content':     json.dumps(
                                {'success': bool(orch_context),
                                 'context': orch_context[:1200]},
                                ensure_ascii=False),
                        })
                        continue   # 跳过下面的 HITL/MCP/Tools/Skills 通用路径

                    # ── Function Call ─────────────────────────
                    params_str = json.dumps(tb.input, ensure_ascii=False)
                    yield mod('function_call', 'processing',
                              f'tool_use_id: {tb.id[:12]} | 工具: {tb.name} | 参数: {params_str}')
                    await asyncio.sleep(0.10)
                    yield mod('function_call', 'done',
                              f'JSON 参数已解析 → {tb.name}({params_str[:80]})')

                    # ── HITL：记忆写入操作在执行前请求人工审核 ──
                    hitl_needed = tb.name in ('save_to_memory', 'add_to_memory_bank')
                    if hitl_needed:
                        op_detail = (f'category={tb.input.get("category","?")} content="{tb.input.get("content","")[:30]}"'
                                     if tb.name == 'save_to_memory' else
                                     f'title="{tb.input.get("title","")[:30]}"')
                        yield mod('hitl', 'processing',
                                  f'操作: {tb.name} | {op_detail} | 等待人工审核 (60s 超时自动批准)…')
                        approval_id = hitl_queue.create_approval(tb.name, tb.input)
                        yield mod('hitl', 'processing',
                                  f'审核 ID: {approval_id} | POST /api/hitl/{approval_id}/approve 批准 | /reject 拒绝',
                                  detail={'approval_id': approval_id, 'operation': tb.name,
                                          'input': tb.input,
                                          'approve_url': f'/api/hitl/{approval_id}/approve',
                                          'reject_url':  f'/api/hitl/{approval_id}/reject',
                                          'timeout_seconds': 60})
                        approved = await hitl_queue.wait_for_decision(approval_id)
                        if not approved:
                            yield mod('hitl', 'blocked',
                                      f'已拒绝 | 操作: {tb.name} | {op_detail} | 跳过执行')
                            tool_results.append({
                                'type':        'tool_result',
                                'tool_use_id': tb.id,
                                'content':     json.dumps(
                                    {'rejected': True, 'reason': '用户通过 HITL 拒绝了此操作'},
                                    ensure_ascii=False),
                            })
                            continue
                        yield mod('hitl', 'done', f'已批准 | 操作: {tb.name} | {op_detail} | 继续执行')

                    # ── MCP 协议路由 ──────────────────────────
                    call_num = len(mcp_server.call_history()) + 1
                    yield mod('mcp', 'processing',
                              f'Call #{call_num} | {tb.name} | 参数: {json.dumps(tb.input, ensure_ascii=False)[:60]}')

                    # ── RAG（知识检索类工具）──────────────────
                    if tb.name in ('search_knowledge_base', 'search_memory_bank'):
                        src   = 'KNOWLEDGE_BASE (17条)' if tb.name == 'search_knowledge_base' else f'memory_bank ({memory_bank.size()}篇)'
                        query = tb.input.get('query', '')
                        yield mod('rag', 'processing', f'query="{query}" → 检索 {src}…')
                        await asyncio.sleep(0.15)

                    # ── Tools 执行（通过 MCP executor 路由）──
                    yield mod('tools', 'processing',
                              f'执行 {tb.name} | 参数: {json.dumps(tb.input, ensure_ascii=False)[:60]}')
                    await asyncio.sleep(0.15)

                    mcp_resp    = mcp_server.call_tool(tb.name, tb.input, executor=_executor)
                    is_error    = mcp_resp.get('isError', False)
                    result_data = mcp_resp.get('_result', {}) if not is_error else \
                                  {'error': mcp_resp['content'][0]['text']}

                    result_summary = _fmt_tool_result(tb.name, result_data)
                    yield mod('tools', 'done', f'{tb.name} → {result_summary}',
                              detail={'tool': tb.name, 'input': tb.input,
                                      'result': result_data, 'via_mcp': True})
                    yield mod('mcp', 'done',
                              f'Call #{call_num} 完成 | {"ERROR" if is_error else "OK"} | {result_summary[:60]}',
                              detail={'call_number': call_num, 'tool': tb.name,
                                      'status': 'error' if is_error else 'ok',
                                      'history': mcp_server.call_history(5)})
                    tools_used.append(tb.name)

                    if tb.name in ('search_knowledge_base', 'search_memory_bank'):
                        yield mod('rag', 'done', f'检索完成 | {result_summary}')

                    # ── Skills：对工具结果做后处理 ────────────
                    skill_result, skill_name = skills_activate(tb.name, result_data)
                    if skill_name:
                        yield mod('skills', 'processing',
                                  f'激活: {skill_name} | 输入: {str(result_data)[:60]}')
                        await asyncio.sleep(0.08)
                        result_data = skill_result
                        yield mod('skills', 'done', _skill_log(skill_name, skill_result),
                                  detail={'skill': skill_name,
                                          'enhancements': {k: v for k, v in skill_result.items()
                                                           if k.startswith('_skill')}})

                    tool_results.append({
                        'type':        'tool_result',
                        'tool_use_id': tb.id,
                        'content':     json.dumps(result_data, ensure_ascii=False),
                    })

                cursor = [
                    *cursor,
                    {'role': 'assistant', 'content': [b.model_dump() for b in resp.content]},
                    {'role': 'user',      'content': tool_results},
                ]
                yield mod('llm', 'processing', f'整合工具结果，生成最终回答… | messages: {len(cursor)} 条',
                          detail={
                              'model':         'claude-haiku-4-5-20251001',
                              'max_tokens':    _max_tok,
                              'system_prompt': sys_prompt,
                              'messages':      [{'role': m['role'],
                                                 'content': (m['content'][:200]
                                                             if isinstance(m['content'], str)
                                                             else f'[{type(m["content"]).__name__}, len={len(m["content"])}]')}
                                                for m in cursor[-6:]],
                          })


        # ════════════════════════════════════════════
        #  Step 8 · Guardrails（输出护栏）
        # ════════════════════════════════════════════
        yield mod('guardrails', 'processing',
                  f'输出检查: 扫描 {len(final_text)} 字符 | 规则: API Key 泄露 / 密码信息…')
        await asyncio.sleep(0.10)

        output_safety = check_output(final_text)
        if not output_safety['safe']:
            final_text = f'抱歉，生成的内容触发了安全护栏（{output_safety["reason"]}），已被过滤。'
            yield mod('guardrails', 'blocked', f'输出拦截: {output_safety["reason"]}')
        else:
            preview = final_text[:60].replace('\n', ' ')
            yield mod('guardrails', 'done',
                      f'输出通过 | {len(final_text)} 字符 | 预览: "{preview}…"')


        # Step 9 已移除：auto_extract_session 被删除
        # 事实提取由 LLM 通过 save_to_memory 工具完成，写入 Long-term Memory


        # ════════════════════════════════════════════
        #  Step 10 · Evaluation（质量评估）
        # ════════════════════════════════════════════
        yield mod('evaluation', 'processing', '评估回答质量…')
        await asyncio.sleep(0.15)
        from modules.evaluation import score_response
        eval_report = build_eval_report(final_text, tools_used)
        tools_str   = ', '.join(tools_used) if tools_used else '无'
        yield mod('evaluation', 'done',
                  f'{eval_report} | 工具: [{tools_str}] | 回答长度: {len(final_text)} 字符',
                  detail={
                      'score':           score_response(final_text, tools_used),
                      'breakdown': {
                          'base_score':    0.72,
                          'length_bonus':  (0.05 if len(final_text) > 80 else 0) +
                                           (0.05 if len(final_text) > 250 else 0),
                          'tool_bonus':    0.10 if tools_used else 0,
                          'format_bonus':  0.03 if ('**' in final_text or '•' in final_text) else 0,
                      },
                      'tools_used':      tools_used,
                      'response_length': len(final_text),
                      'response_preview': final_text[:400],
                  })

        session['history'].append({'role': 'user',      'content': message})
        session['history'].append({'role': 'assistant', 'content': final_text})

        yield sse('text', {'text': final_text})
        yield sse('done', {})

    except Exception as e:
        print(f'[Pipeline Error] {e}')
        yield sse('error', {'message': str(e) or 'API 调用失败，请检查 API Key'})


# ─────────────────────────────────────────────
#  静态文件
# ─────────────────────────────────────────────
app.mount('/', StaticFiles(directory=os.path.dirname(__file__), html=True), name='static')


if __name__ == '__main__':
    import uvicorn
    print('\nAI Agent 演示服务器已启动（Python 版本）')
    print('   浏览器打开: http://localhost:3000')
    print('   各模块代码: ./modules/\n')
    uvicorn.run('server:app', host='0.0.0.0', port=3000, reload=False)
