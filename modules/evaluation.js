/**
 * ╔══════════════════════════════════════════════════════╗
 * ║                 Evaluation 模块                      ║
 * ║                                                      ║
 * ║  对 Agent 的最终回答进行质量评分                      ║
 * ║                                                      ║
 * ║  评估维度：                                           ║
 * ║    - 回答完整性（长度是否足够）                       ║
 * ║    - 工具使用情况（是否恰当调用了工具）               ║
 * ║    - 结构化程度（是否使用了列表、加粗等格式）          ║
 * ║                                                      ║
 * ║  注：这是演示级实现，生产环境的 Evaluation 通常使用：  ║
 * ║    - LLM-as-judge：用另一个模型评估输出质量           ║
 * ║    - Golden dataset：对比标准答案计算准确率           ║
 * ║    - 用户反馈：收集点赞/踩的信号                      ║
 * ╚══════════════════════════════════════════════════════╝
 */

'use strict';

// ─────────────────────────────────────────────
//  对回答进行综合评分，返回 0.00 ~ 0.99 的浮点数
//
//  参数：
//    answer     - LLM 生成的最终回答文本
//    toolsUsed  - 本次调用了哪些工具（字符串数组）
// ─────────────────────────────────────────────
function scoreResponse(answer, toolsUsed) {
  let score = 0.72;  // 基础分

  // 完整性评分：回答足够长说明内容充实
  if (answer.length >  80) score += 0.05;
  if (answer.length > 250) score += 0.05;

  // 工具使用奖励：恰当调用工具说明 Agent 能力完整发挥
  if (toolsUsed.length > 0) score += 0.10;

  // 结构化奖励：使用列表或加粗等格式说明回答组织清晰
  if (/[①②③•\-]/.test(answer) || answer.includes('**')) score += 0.03;

  // 上限 0.99（满分 1.00 保留给人工审核）
  return Math.min(score, 0.99).toFixed(2);
}

// ─────────────────────────────────────────────
//  生成评估报告（用于 Pipeline 日志展示）
// ─────────────────────────────────────────────
function buildEvalReport(answer, toolsUsed) {
  const score       = scoreResponse(answer, toolsUsed);
  const completeness = answer.length > 100 ? '高' : '中';
  const toolReport   = toolsUsed.length > 0
    ? `工具调用 ${toolsUsed.length} 次（${toolsUsed.join(', ')}）`
    : '无工具调用';

  return `评分 ${score} | ${toolReport} | 完整性：${completeness}`;
}

module.exports = { scoreResponse, buildEvalReport };
