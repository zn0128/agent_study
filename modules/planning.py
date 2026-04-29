"""
╔══════════════════════════════════════════════════════╗
║                  Planning 模块                       ║
║                                                      ║
║  负责分析用户意图，为 Pipeline 制定处理策略           ║
║                                                      ║
║  三种推理模式：                                       ║
║                                                      ║
║  CoT（Chain-of-Thought，链式思考）                   ║
║    适合：直接问答，无需外部工具                       ║
║    流程：问题 → 逐步推理 → 答案                      ║
║                                                      ║
║  ReAct（Reasoning + Acting）                        ║
║    适合：需要调用工具的任务                           ║
║    流程：推理 → 行动（调用工具）→ 观察结果 → 再推理   ║
║                                                      ║
║  Plan-and-Execute（计划后执行）                      ║
║    适合：复杂的多步骤任务                             ║
║    流程：制定完整计划 → 分配子任务 → 逐步执行 → 整合  ║
╚══════════════════════════════════════════════════════╝
"""

import re

# ─────────────────────────────────────────────
#  复杂任务识别：匹配需要多步骤、深度分析的关键词
#  触发 Plan-and-Execute 模式 + Orchestrator + SubAgent
# ─────────────────────────────────────────────
_COMPLEX_TASK_PATTERN = re.compile(r'分析|报告|制定|规划|方案|总结|研究|调研|帮我写')


def analyze_task(message: str) -> dict:
    """
    分析任务类型，返回处理策略

    返回：
      mode       - 推理模式（cot / react / plan-and-execute）
      is_complex - 是否为复杂任务（影响是否启用 Orchestrator/SubAgent）
      mode_desc  - 模式描述（用于 Pipeline 日志展示）
    """
    is_complex = bool(_COMPLEX_TASK_PATTERN.search(message))

    if is_complex:
        return {
            'mode':       'plan-and-execute',
            'is_complex': True,
            'mode_desc':  'Plan-and-Execute：检测到复杂任务，分解子步骤',
        }

    # 对于非复杂任务，实际使用哪种模式由 LLM 自主决定（ReAct or CoT）
    return {
        'mode':       'react-cot',
        'is_complex': False,
        'mode_desc':  'ReAct / CoT：分析意图，判断是否需要调用工具',
    }
