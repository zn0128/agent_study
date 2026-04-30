/**
 * ╔══════════════════════════════════════════════════════╗
 * ║                  Prompt 模块                         ║
 * ║                                                      ║
 * ║  负责构建发送给 LLM 的系统提示词（System Prompt）     ║
 * ║                                                      ║
 * ║  系统 Prompt 的作用：                                ║
 * ║    1. 定义 Agent 的角色和行为边界                    ║
 * ║    2. 列出可用工具及使用指南（Function Call 的基础）  ║
 * ║    3. 注入 Memory 检索结果（让 LLM 知道历史信息）     ║
 * ║                                                      ║
 * ║  与 Context 的区别：                                 ║
 * ║    Prompt   - 系统级指令，每次对话都存在，对用户不可见 ║
 * ║    Context  - 包含 Prompt + 对话历史 + 工具结果的总和 ║
 * ╚══════════════════════════════════════════════════════╝
 */

'use strict';

// ─────────────────────────────────────────────
//  构建系统提示词
//
//  参数：
//    sessionFacts  - 会话内记忆（Layer 1）
//    vectorHits    - 向量检索命中的持久化记忆（Layer 2+3）
//
//  记忆注入策略：
//    将检索到的记忆拼接到系统 Prompt 末尾
//    LLM 在推理时会自动参考这些历史信息
//    这是让 LLM "记住"历史的核心机制
// ─────────────────────────────────────────────
function buildSystemPrompt(sessionFacts, vectorHits) {

  // ── 基础指令：角色定义 + 工具使用指南 ─────────
  let prompt = `你是一个智能 AI 助手，运行在 AI Agent 模块演示平台上。你可以使用以下工具：
- search_knowledge_base：搜索 AI Agent 相关知识（模块定义、架构、概念等）
- calculate：执行数学计算
- get_datetime：获取当前日期时间
- save_to_memory：将重要信息保存到持久化记忆（跨会话有效）

工具使用规则：
1. 用户询问 AI Agent 相关概念时，优先调用 search_knowledge_base
2. 涉及计算时使用 calculate
3. 询问时间日期时使用 get_datetime
4. 用户明确要求记住某事时使用 save_to_memory

请用中文回答，简洁清晰，突出重点。`;

  // ── 注入会话内记忆（Layer 1）─────────────────
  // 这些是本次会话中提取的临时事实，LLM 可以直接引用
  if (sessionFacts.length > 0) {
    prompt += '\n\n[会话内记忆 — 本次会话中提取的信息]\n';
    prompt += sessionFacts.map(m => `- [${m.category}] ${m.content}`).join('\n');
  }

  // ── 注入持久化记忆（Layer 2+3，向量语义检索结果）─
  // 这些是跨会话的长期记忆，按相关度排序后注入
  if (vectorHits.length > 0) {
    prompt += '\n\n[长期记忆 — 向量语义检索结果（按相关度排序）]\n';
    prompt += vectorHits
      .map(m => `- [${m.category}] ${m.content}（相关度 ${m.score.toFixed(2)}）`)
      .join('\n');
  }

  return prompt;
}

module.exports = { buildSystemPrompt };
