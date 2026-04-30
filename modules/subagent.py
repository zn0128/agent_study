"""
╔══════════════════════════════════════════════════════╗
║                  SubAgent 模块                       ║
║                                                      ║
║  SubAgent = 主 Agent 委派的专项执行单元               ║
║                                                      ║
║  与主 Agent 的区别：                                 ║
║    主 Agent   → 完整工具集，多轮对话，完整记忆        ║
║    SubAgent   → 聚焦单一子任务，独立 Context，轻量    ║
║                                                      ║
║  调用时机：                                          ║
║    Planning 判断为复杂任务时，Orchestrator 调度       ║
║    SubAgent 完成子任务后将结果返回主 Agent            ║
║                                                      ║
║  实现：独立的 Claude API 调用（非模拟）               ║
╚══════════════════════════════════════════════════════╝
"""

import httpx
import anthropic

SUBAGENT_SYSTEM = (
    '你是一个专项执行 SubAgent，由主 Agent 委派处理特定子任务。'
    '请聚焦、简洁地完成任务，输出结构化结果供主 Agent 汇总使用。'
    '不要闲聊，直接给出结论。'
)


async def run_subagent(task: str, api_key: str) -> dict:
    """
    以独立 Claude 调用执行单一子任务。

    参数：
      task    - 具体的子任务描述
      api_key - Anthropic API Key

    返回：
      { success, task, result, tokens } 或 { success: False, error }
    """
    try:
        client = anthropic.AsyncAnthropic(
            api_key=api_key,
            http_client=httpx.AsyncClient(verify=False, timeout=15),  # 每个 SubAgent 最多 15s
        )
        resp = await client.messages.create(
            model      = 'claude-haiku-4-5-20251001',
            max_tokens = 512,
            system     = SUBAGENT_SYSTEM,
            messages   = [{'role': 'user', 'content': task}],
        )
        text = next((b.text for b in resp.content if b.type == 'text'), '')
        return {
            'success': True,
            'task':    task,
            'result':  text,
            'tokens':  resp.usage.output_tokens,
        }
    except anthropic.AuthenticationError:
        return {'success': False, 'task': task, 'error': 'API Key 无效'}
    except Exception as e:
        return {'success': False, 'task': task, 'error': str(e)}
