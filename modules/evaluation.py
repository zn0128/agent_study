"""
╔══════════════════════════════════════════════════════╗
║                 Evaluation 模块                      ║
║                                                      ║
║  对 Agent 的最终回答进行质量评分                      ║
║                                                      ║
║  评估维度：                                           ║
║    - 回答完整性（长度是否足够）                       ║
║    - 工具使用情况（是否恰当调用了工具）               ║
║    - 结构化程度（是否使用了列表、加粗等格式）          ║
║                                                      ║
║  注：这是演示级实现，生产环境的 Evaluation 通常使用：  ║
║    - LLM-as-judge：用另一个模型评估输出质量           ║
║    - Golden dataset：对比标准答案计算准确率           ║
║    - 用户反馈：收集点赞/踩的信号                      ║
╚══════════════════════════════════════════════════════╝
"""

import re


def score_response(answer: str, tools_used: list[str]) -> str:
    """
    对回答进行综合评分，返回 '0.00' ~ '0.99' 的字符串

    参数：
      answer     - LLM 生成的最终回答文本
      tools_used - 本次调用了哪些工具（字符串列表）
    """
    score = 0.72  # 基础分

    # 完整性评分：回答足够长说明内容充实
    if len(answer) >  80: score += 0.05
    if len(answer) > 250: score += 0.05

    # 工具使用奖励：恰当调用工具说明 Agent 能力完整发挥
    if len(tools_used) > 0: score += 0.10

    # 结构化奖励：使用列表或加粗等格式说明回答组织清晰
    if re.search(r'[①②③•\-]', answer) or '**' in answer:
        score += 0.03

    # 上限 0.99（满分 1.00 保留给人工审核）
    return f'{min(score, 0.99):.2f}'


def build_eval_report(answer: str, tools_used: list[str]) -> str:
    """生成评估报告（用于 Pipeline 日志展示）"""
    score       = score_response(answer, tools_used)
    completeness = '高' if len(answer) > 100 else '中'
    tool_report  = (
        f'工具调用 {len(tools_used)} 次（{", ".join(tools_used)}）'
        if tools_used else '无工具调用'
    )
    return f'评分 {score} | {tool_report} | 完整性：{completeness}'
