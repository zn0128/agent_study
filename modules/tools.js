/**
 * ╔══════════════════════════════════════════════════════╗
 * ║              Tools + Function Call 模块              ║
 * ║                                                      ║
 * ║  两个概念，一个文件，因为它们紧密配合：               ║
 * ║                                                      ║
 * ║  Function Call（函数调用）                           ║
 * ║    - LLM 决定调用哪个工具、生成结构化参数             ║
 * ║    - 这是 LLM 侧的能力（输出 JSON 而非文本）          ║
 * ║    - 本文件定义工具的"接口描述"（给 LLM 看的说明书）  ║
 * ║                                                      ║
 * ║  Tools（工具执行）                                   ║
 * ║    - Agent 框架捕获 LLM 的 JSON 指令后实际执行        ║
 * ║    - 这是服务器侧的能力（真实计算、文件读写等）        ║
 * ║    - 本文件实现各工具的具体逻辑                       ║
 * ║                                                      ║
 * ║  调用链：                                            ║
 * ║    用户消息 → LLM 推理 → 输出 Function Call JSON      ║
 * ║    → executeTool() 执行 → 结果回传 LLM → 最终回答    ║
 * ╚══════════════════════════════════════════════════════╝
 */

'use strict';

const { searchKnowledgeBase } = require('./rag');

// ─────────────────────────────────────────────
//  工具接口描述（传给 Claude API 的 tools 参数）
//
//  这是 Function Call 的核心：LLM 读取这些描述，
//  自主决定什么时候调用哪个工具、填什么参数。
//  description 写得越清晰，LLM 决策越准确。
// ─────────────────────────────────────────────
const TOOL_DEFINITIONS = [
  {
    name:        'search_knowledge_base',
    description: '搜索 AI Agent 知识库，获取关于 Agent 各模块（Memory、RAG、MCP、Planning、Guardrails 等）的详细解释。用户询问 AI Agent 相关概念时必须使用此工具。',
    input_schema: {
      type:       'object',
      properties: { query: { type: 'string', description: '搜索关键词或问题' } },
      required:   ['query'],
    },
  },
  {
    name:        'calculate',
    description: '执行数学计算，支持加减乘除、幂运算、括号等。',
    input_schema: {
      type:       'object',
      properties: { expression: { type: 'string', description: '数学表达式，如 "2 + 3 * 4"' } },
      required:   ['expression'],
    },
  },
  {
    name:        'get_datetime',
    description: '获取当前日期和时间。',
    input_schema: { type: 'object', properties: {} },
  },
  {
    name:        'save_to_memory',
    description: '将用户提供的重要信息或偏好保存到长期持久化记忆，跨会话有效。用户明确要求记住某事时使用。',
    input_schema: {
      type:       'object',
      properties: {
        category: { type: 'string', description: '记忆类别，如 "用户偏好"、"项目信息"' },
        content:  { type: 'string', description: '要保存的具体内容' },
      },
      required: ['category', 'content'],
    },
  },
];

// ─────────────────────────────────────────────
//  工具执行函数（Tools 层）
//
//  参数：
//    name       - 工具名称（来自 LLM 的 Function Call）
//    input      - 工具参数（LLM 自动生成的结构化 JSON）
//    session    - 当前会话（某些工具需要操作会话数据）
//    addMemory  - 添加持久化记忆的回调函数（由 memory 模块提供）
// ─────────────────────────────────────────────
function executeTool(name, input, session, addMemory) {
  switch (name) {

    // ── 工具 1：知识库检索（配合 RAG 模块）──────
    case 'search_knowledge_base':
      return searchKnowledgeBase(input.query);

    // ── 工具 2：数学计算 ────────────────────────
    case 'calculate': {
      try {
        // 安全过滤：只允许数字和基础运算符，防止代码注入
        const safe = input.expression.replace(/[^0-9+\-*/.()% ]/g, '');
        if (!safe.trim()) return { error: '无效表达式' };
        // eslint-disable-next-line no-new-func
        const result = Function('"use strict"; return (' + safe + ')')();
        return { expression: input.expression, result: +result.toFixed(10) };
      } catch (e) {
        return { error: '计算失败: ' + e.message };
      }
    }

    // ── 工具 3：获取当前时间 ────────────────────
    case 'get_datetime': {
      const now = new Date();
      return {
        date: now.toLocaleDateString('zh-CN', {
          year: 'numeric', month: 'long', day: 'numeric', weekday: 'long',
        }),
        time: now.toLocaleTimeString('zh-CN'),
      };
    }

    // ── 工具 4：保存记忆（联动 Memory 模块）───────
    case 'save_to_memory': {
      const entry = {
        id:       Date.now().toString(),
        category: input.category,
        content:  input.content,
        at:       new Date().toISOString(),
        source:   'explicit',  // 区别于 autoExtract 的自动提取
      };
      // 写入持久化存储 + 向量索引（由 memory 模块处理）
      addMemory(entry);
      // 同步到会话内事实，本次会话立即可用
      session.sessionFacts.push(entry);
      return { success: true, saved: `[${entry.category}] ${entry.content}`, persisted: true };
    }

    default:
      return { error: `未知工具: ${name}` };
  }
}

module.exports = { TOOL_DEFINITIONS, executeTool };
