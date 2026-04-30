/**
 * ╔══════════════════════════════════════════════════════╗
 * ║                   RAG 模块                           ║
 * ║                                                      ║
 * ║  Retrieval-Augmented Generation（检索增强生成）       ║
 * ║                                                      ║
 * ║  工作流程：                                           ║
 * ║    1. 维护一个本地知识库（AI Agent 模块定义）          ║
 * ║    2. 当 Claude 调用 search_knowledge_base 工具时     ║
 * ║    3. 用关键词匹配从知识库召回相关片段                 ║
 * ║    4. 片段作为工具结果回传给 Claude                   ║
 * ║    5. Claude 基于真实文档生成有据可查的回答            ║
 * ║                                                      ║
 * ║  与纯 LLM 回答的区别：                               ║
 * ║    纯 LLM → 依赖训练知识，可能幻觉、知识截止          ║
 * ║    RAG    → 基于真实文档，准确、可更新、可溯源         ║
 * ╚══════════════════════════════════════════════════════╝
 */

'use strict';

// ─────────────────────────────────────────────
//  知识库：AI Agent 各模块的权威定义
//  这里扮演"外部知识库"的角色（真实场景会是向量数据库）
//
//  每条记录：
//    topic   - 模块名称（用于展示）
//    kw      - 关键词列表（用于匹配查询）
//    content - 详细内容（回传给 LLM 的知识片段）
// ─────────────────────────────────────────────
const KNOWLEDGE_BASE = [
  {
    topic:   'Agent（智能体）',
    kw:      ['agent', '智能体', '主体', '自主'],
    content: 'Agent 是具备自主决策能力的智能软件。能感知输入、规划行动、调用工具并持续与用户交互。核心循环：感知→推理→行动→观察→迭代。',
  },
  {
    topic:   'LLM（大语言模型）',
    kw:      ['llm', '大语言模型', '语言模型', 'gpt', 'claude'],
    content: 'LLM 是 Agent 的核心推理引擎，负责理解语言、分析意图、生成文本。代表模型：GPT-4、Claude、Gemini。',
  },
  {
    topic:   'Prompt（提示词）',
    kw:      ['prompt', '提示词', '系统提示', '指令'],
    content: '提示词是输入给 LLM 的指令与上下文。系统 Prompt 定义 Agent 角色；用户 Prompt 是具体请求；Few-shot 提供参考示例。',
  },
  {
    topic:   'Context（上下文）',
    kw:      ['context', '上下文', '窗口', '对话历史'],
    content: 'Context 是 LLM 单次推理可见的全部文本：系统指令、对话历史、检索内容、工具结果等。有长度上限（Context Window），超出需截断或压缩。',
  },
  {
    topic:   'Planning / Reasoning（规划与推理）',
    kw:      ['planning', 'reasoning', '规划', '推理', 'react', 'cot'],
    content: '规划推理让 Agent 先思考再行动。①CoT 链式思考逐步推导；②ReAct 推理+行动交替，观察结果后继续推理；③Plan-and-Execute 先制定完整计划再执行。',
  },
  {
    topic:   'Memory（记忆）',
    kw:      ['memory', '记忆', '长期记忆', '短期记忆', '持久化', '向量'],
    content: 'Memory 分三层：①会话内记忆（当前 Context 中的临时事实，会话结束失效）；②持久化记忆（写入磁盘，跨会话有效）；③向量检索（TF-IDF 语义匹配，按相关度召回）。',
  },
  {
    topic:   'RAG（检索增强生成）',
    kw:      ['rag', 'retrieval', '检索增强', '向量检索', '知识库'],
    content: 'RAG 先语义检索外部知识库，再将相关片段与问题一同输入 LLM。优势：减少幻觉、知识可更新（无需重训模型）、输出可溯源。',
  },
  {
    topic:   'Tools（工具）',
    kw:      ['tools', '工具', '搜索', '代码执行'],
    content: 'Tools 是 Agent 可调用的外部能力实体：搜索引擎、计算器、文件系统、API 等。Agent 的能力边界取决于它有哪些 Tools。Function Call 是调用机制，Tools 是被调用的对象。',
  },
  {
    topic:   'Function Call（函数调用）',
    kw:      ['function call', '函数调用', '工具调用', '结构化'],
    content: 'Function Call 是 LLM 使用工具的核心机制：LLM 输出结构化 JSON 而非普通文本，Agent 框架捕获后执行工具，将结果回传 LLM 再生成最终答案。',
  },
  {
    topic:   'MCP（模型上下文协议）',
    kw:      ['mcp', '模型上下文协议', '协议', '标准'],
    content: 'MCP 是 Anthropic 提出的工具集成标准。开发者按规范包装任意 API，Agent 通过统一协议发现并调用，无需逐一适配。类比：USB 标准。',
  },
  {
    topic:   'Skills（技能）',
    kw:      ['skills', '技能', '能力单元', '复用'],
    content: 'Skills 是 Agent 可复用的能力单元，封装好的专业能力（如"数据分析"、"代码审查"），可视为预打包的 Workflow，随需激活。',
  },
  {
    topic:   'Workflow（工作流）',
    kw:      ['workflow', '工作流', '流程', '硬编码'],
    content: 'Workflow 是人预定义的标准化执行步骤（硬编码）。与 Planning 区别：Workflow 是固定流程（确定性）；Planning 是运行时动态生成（灵活性）。',
  },
  {
    topic:   'SubAgent（子智能体）',
    kw:      ['subagent', '子智能体', '子agent', '委派'],
    content: 'SubAgent 是主 Agent 委派的专项执行角色，拥有独立的 Context 和工具集，完成后将结果返回主对话。支持嵌套。',
  },
  {
    topic:   'Orchestrator（编排器）',
    kw:      ['orchestrator', '编排器', '调度', '多agent'],
    content: 'Orchestrator 是多 Agent 系统的调度中枢：任务分解、SubAgent 调度、上下文传递、结果聚合、异常处理。SubAgent 是"干活的人"，Orchestrator 是"项目经理"。',
  },
  {
    topic:   'Guardrails（护栏）',
    kw:      ['guardrails', '护栏', '安全', '过滤', '拦截'],
    content: 'Guardrails 双向防线：①输入端拦截提示词注入、越权请求；②输出端检测幻觉、过滤敏感信息、阻断高风险操作。',
  },
  {
    topic:   'Human-in-the-loop（人工介入）',
    kw:      ['hitl', 'human', '人工介入', '人工确认'],
    content: 'HITL 在高风险节点暂停 Agent 请求人工确认。三类时机：执行前审批、过程中纠偏、结果后审核。与 Guardrails 区别：Guardrails 是机器自动拦截，HITL 是将决策权交还给人。',
  },
  {
    topic:   'Evaluation（评估）',
    kw:      ['evaluation', '评估', '质量', '评分', '度量'],
    content: 'Evaluation 对 Agent 输出持续度量：任务完成率、工具调用准确率、推理路径质量、延迟与成本、安全合规性。驱动 Prompt/Tools/Workflow 迭代优化。',
  },
  {
    topic:   'Langchain（编程框架）',
    kw:      ['langchain', '框架', '编程框架'],
    content: 'Langchain 提供 Agent、Memory、Tools、Chain 等标准化组件，简化 AI 应用开发。属实现框架层，是构建 Agent 的"工具箱"，非 Agent 本身的运行模块。',
  },
];

// ─────────────────────────────────────────────
//  知识库检索函数
//  策略：关键词命中（查询词包含 kw 中任意一项，或 topic 名称）
//  真实场景会改用向量数据库的语义检索
// ─────────────────────────────────────────────
function searchKnowledgeBase(query) {
  const q    = query.toLowerCase();
  const hits = KNOWLEDGE_BASE.filter(item =>
    item.kw.some(k => q.includes(k)) ||
    item.topic.toLowerCase().replace(/[（）()]/g, '').includes(q)
  ).slice(0, 3);  // 最多返回 3 个片段，避免 Context 过长

  if (!hits.length) {
    return { found: false, message: '知识库中未找到相关内容，将根据训练知识回答。' };
  }

  return {
    found:   true,
    count:   hits.length,
    results: hits.map(h => ({ topic: h.topic, content: h.content })),
  };
}

module.exports = { KNOWLEDGE_BASE, searchKnowledgeBase };
