"""
╔══════════════════════════════════════════════════════╗
║                  Planning 模块                       ║
║                                                      ║
║  负责分析用户意图，为 Pipeline 制定处理策略           ║
║                                                      ║
║  两种判断方式（自动降级）：                           ║
║                                                      ║
║  方式 1：LLM 分类（classify_task）                   ║
║    调用 Claude 判断任务是否需要多步骤分解             ║
║    准确，但多一次 API 调用（~2s）                    ║
║                                                      ║
║  方式 2：关键词匹配（analyze_task，降级兜底）         ║
║    纯本地正则，无 API 调用                           ║
║    不够准确，但零延迟                                ║
║                                                      ║
║  三种推理模式：                                       ║
║    CoT         → 直接问答，无工具                    ║
║    ReAct       → 需要调用工具                        ║
║    Plan-and-Execute → 多步骤，需分解 + SubAgent      ║
╚══════════════════════════════════════════════════════╝
"""

import re

import anthropic
import httpx

# ─────────────────────────────────────────────
#  LLM 分类器系统提示
#  只返回结构化结论，max_tokens=60 保证快速
# ─────────────────────────────────────────────
_CLASSIFY_SYSTEM = """你是任务复杂度分类器。

判断用户请求是否需要"多步骤分解"才能完成（即需要 Plan-and-Execute 模式）。

需要分解的特征：
- 明确要求生成完整文档/报告/方案
- 包含多个独立子目标
- 需要收集、分析、整合多方面内容

不需要分解的特征：
- 单一问题（查询、计算、翻译）
- 对话/闲聊
- 简单指令

只输出以下格式，不要解释：
COMPLEX: yes/no
REASON: 一句话理由
MODE: plan-and-execute / react-cot"""


async def classify_task(message: str, api_key: str) -> dict:
    """
    用 LLM 判断任务是否需要多步骤分解。

    比关键词匹配更准确：
      "帮我写 hello"        → no（虽含"帮我写"，但任务极简单）
      "分析一下这道题"      → no（单一问题）
      "写一份季度市场分析"  → yes（需要收集+分析+撰写）

    返回：
      is_complex  - bool
      mode        - plan-and-execute / react-cot
      mode_desc   - 描述（含 LLM 给出的理由）
      method      - 'llm'（标记来源）
    """
    try:
        client = anthropic.AsyncAnthropic(
            api_key=api_key,
            http_client=httpx.AsyncClient(verify=False, timeout=10),
        )
        resp = await client.messages.create(
            model      = 'claude-haiku-4-5-20251001',
            max_tokens = 60,
            system     = _CLASSIFY_SYSTEM,
            messages   = [{'role': 'user', 'content': message}],
        )
        text = next((b.text for b in resp.content if b.type == 'text'), '')

        # 解析输出
        is_complex = 'yes' in text.lower().split('complex:')[-1].split('\n')[0].lower()
        reason     = ''
        mode_line  = 'react-cot'
        for line in text.split('\n'):
            if line.lower().startswith('reason:'):
                reason = line.split(':', 1)[-1].strip()
            if line.lower().startswith('mode:'):
                mode_line = line.split(':', 1)[-1].strip().lower()

        mode = 'plan-and-execute' if is_complex else 'react-cot'
        return {
            'mode':          mode,
            'is_complex':    is_complex,
            'mode_desc':     f'{"Plan-and-Execute" if is_complex else "ReAct / CoT"}：{reason}',
            'method':        'llm',
            'llm_output':    text.strip(),
            'tokens':        resp.usage.input_tokens + resp.usage.output_tokens,
            'system_prompt': _CLASSIFY_SYSTEM,
            'input_message': message,
        }

    except Exception as e:
        # LLM 调用失败 → 降级到关键词匹配
        fallback = analyze_task(message)
        fallback['method']  = 'fallback'
        fallback['error']   = str(e)
        return fallback


# ─────────────────────────────────────────────
#  关键词匹配（兜底/降级用）
# ─────────────────────────────────────────────
_COMPLEX_TASK_PATTERN = re.compile(r'分析|报告|制定|规划|方案|总结|研究|调研|帮我写')


def analyze_task(message: str) -> dict:
    """
    纯本地关键词匹配，零延迟，作为 classify_task 的降级兜底。
    直接在 pipeline 中不调 API 时使用。
    """
    is_complex = bool(_COMPLEX_TASK_PATTERN.search(message))

    if is_complex:
        return {
            'mode':       'plan-and-execute',
            'is_complex': True,
            'mode_desc':  'Plan-and-Execute：检测到复杂任务关键词',
            'method':     'keyword',
        }

    return {
        'mode':       'react-cot',
        'is_complex': False,
        'mode_desc':  'ReAct / CoT：分析意图，判断是否需要调用工具',
        'method':     'keyword',
    }
