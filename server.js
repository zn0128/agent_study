/**
 * ╔══════════════════════════════════════════════════════╗
 * ║                   server.js                          ║
 * ║                                                      ║
 * ║  AI Agent 演示服务器                                 ║
 * ║                                                      ║
 * ║  本文件只做两件事：                                   ║
 * ║    1. Express HTTP 服务（接收请求、SSE 推送）         ║
 * ║    2. Pipeline 串联（按顺序调用各模块）               ║
 * ║                                                      ║
 * ║  具体的模块逻辑都在 modules/ 目录下：                 ║
 * ║    memory.js      三层记忆体系                       ║
 * ║    rag.js         知识库检索                         ║
 * ║    guardrails.js  安全护栏                           ║
 * ║    tools.js       工具定义与执行                     ║
 * ║    planning.js    任务规划                           ║
 * ║    prompt.js      提示词构建                         ║
 * ║    evaluation.js  质量评估                           ║
 * ╚══════════════════════════════════════════════════════╝
 */

'use strict';

const express   = require('express');
const Anthropic  = require('@anthropic-ai/sdk');

// ── 导入各模块 ────────────────────────────────
const Memory     = require('./modules/memory');
const { checkInput, checkOutput } = require('./modules/guardrails');
const { TOOL_DEFINITIONS, executeTool } = require('./modules/tools');
const { analyzeTask }    = require('./modules/planning');
const { buildSystemPrompt } = require('./modules/prompt');
const { buildEvalReport }   = require('./modules/evaluation');

// ─────────────────────────────────────────────
//  初始化
// ─────────────────────────────────────────────
const app = express();
app.use(express.json({ limit: '10mb' }));
app.use(express.static(__dirname));

// 启动时加载持久化记忆，建立向量索引
let persistedMem    = Memory.loadPersisted();
const vectorMem     = new Memory.VectorMemory();
vectorMem.rebuild(persistedMem);
console.log(`📦 已加载 ${persistedMem.length} 条持久化记忆`);

// 向 Memory 模块提供"添加持久化记忆"的回调
// （避免 memory.js 直接依赖 persistedMem 变量）
function addPersistedMemory(entry) {
  persistedMem.push(entry);
  vectorMem.add(entry);
  Memory.savePersisted(persistedMem);
}

const wait = ms => new Promise(r => setTimeout(r, ms));

// ─────────────────────────────────────────────
//  API 路由
// ─────────────────────────────────────────────

// 查看所有持久化记忆（调试用）
app.get('/api/memories', (_, res) => {
  res.json({ count: persistedMem.length, memories: persistedMem.slice(-50) });
});

// 主对话端点：POST body → SSE 流式响应
app.post('/api/chat', async (req, res) => {
  const { message, sessionId, apiKey } = req.body;
  if (!message || !apiKey) {
    return res.status(400).json({ error: '缺少 message 或 apiKey' });
  }

  // 建立 SSE 连接
  res.writeHead(200, {
    'Content-Type':  'text/event-stream',
    'Cache-Control': 'no-cache',
    'Connection':    'keep-alive',
  });

  const emit = (event, data) =>
    res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);

  try {
    await runPipeline(message, sessionId, apiKey, emit);
  } catch (err) {
    console.error('[Pipeline Error]', err.message);
    emit('error', { message: err.message || 'API 调用失败，请检查 API Key' });
  }
  res.end();
});

// ─────────────────────────────────────────────
//  Pipeline：按顺序调用各模块
//
//  每个步骤调用 mod() 发送 SSE 事件，
//  前端收到后实时更新模块可视化面板。
// ─────────────────────────────────────────────
async function runPipeline(message, sessionId, apiKey, emit) {
  const session = Memory.getSession(sessionId);

  // 简写：发送模块状态事件
  const mod = (id, status, log) => emit('module', { id, status, log });

  // ════════════════════════════════════════════
  //  常驻模块（始终运行）
  // ════════════════════════════════════════════
  mod('langchain', 'active', '框架初始化，加载运行时组件');
  mod('agent',     'active', '主 Agent 启动，接收用户输入');


  // ════════════════════════════════════════════
  //  Step 1 · Guardrails（输入护栏）
  //  最先执行，拦截后直接返回，不消耗 API 调用
  // ════════════════════════════════════════════
  mod('guardrails', 'processing', '输入安全检查中…');
  await wait(120);

  const inputSafety = checkInput(message);
  if (!inputSafety.safe) {
    mod('guardrails', 'blocked', inputSafety.reason);
    emit('text', { text: `⚠️ 请求已被安全护栏拦截：${inputSafety.reason}` });
    emit('done', {});
    return;
  }
  mod('guardrails', 'done', '安全检查通过，无注入或高风险指令');


  // ════════════════════════════════════════════
  //  Step 2 · Memory（三层记忆检索）
  //  在构建 Prompt 之前检索，结果注入 Prompt
  // ════════════════════════════════════════════
  mod('memory', 'processing', '[1/3] 会话内记忆：检查本次会话的临时事实…');
  await wait(80);

  mod('memory', 'processing',
    `[1/3] 会话内记忆：${session.sessionFacts.length} 条事实`);
  await wait(80);

  mod('memory', 'processing',
    `[2/3] 向量检索：对 ${vectorMem.size()} 条持久化记忆计算 TF-IDF 余弦相似度…`);
  await wait(220);

  const { sessionFacts, vectorHits } = Memory.retrieveAll(message, session, vectorMem);

  mod('memory', 'processing',
    `[3/3] 注入上下文：会话内 ${sessionFacts.length} 条 + 向量命中 ${vectorHits.length} 条`);
  await wait(100);

  const memLog = vectorHits.length > 0
    ? `向量命中 ${vectorHits.length} 条（最高相关度 ${vectorHits[0].score.toFixed(2)}），会话内 ${sessionFacts.length} 条`
    : sessionFacts.length > 0
      ? `仅会话内记忆 ${sessionFacts.length} 条，持久化库无语义命中`
      : `无相关记忆（持久化库共 ${persistedMem.length} 条）`;
  mod('memory', 'done', memLog);


  // ════════════════════════════════════════════
  //  Step 3 · Prompt（构建系统提示词）
  //  将记忆检索结果注入 Prompt
  // ════════════════════════════════════════════
  mod('prompt', 'processing', '构建系统提示词（含记忆注入）…');
  const sysPrompt = buildSystemPrompt(sessionFacts, vectorHits);
  await wait(80);
  mod('prompt', 'done',
    `提示词就绪（${sysPrompt.length} 字符），已注入 ${sessionFacts.length + vectorHits.length} 条记忆`);


  // ════════════════════════════════════════════
  //  Step 4 · Context（构建上下文窗口）
  // ════════════════════════════════════════════
  mod('context', 'processing', '构建上下文窗口…');
  await wait(80);
  const histLen = session.history.length;
  const estPct  = Math.min(
    Math.round((histLen * 120 + message.length + sysPrompt.length) / 200000 * 100),
    99
  );
  mod('context', 'done', `窗口占用约 ${estPct}%，含 ${histLen} 轮历史对话`);


  // ════════════════════════════════════════════
  //  Step 5 · Planning（任务规划）
  // ════════════════════════════════════════════
  mod('planning', 'processing', '分析任务类型，制定处理策略…');
  await wait(240);
  const { isComplex, modeDesc } = analyzeTask(message);
  mod('planning', 'done', modeDesc);


  // ════════════════════════════════════════════
  //  Step 6 · Orchestrator（复杂任务编排）
  //  仅在 Planning 判定为复杂任务时激活
  // ════════════════════════════════════════════
  if (isComplex) {
    mod('orchestrator', 'processing', '任务编排，分配执行单元…');
    await wait(160);
    mod('orchestrator', 'done', '编排就绪，主 Agent 执行，按需调度 SubAgent');
  }


  // ════════════════════════════════════════════
  //  Step 7 · LLM + 工具调用循环（ReAct 核心）
  //
  //  这是 Agent 的核心循环：
  //    LLM 推理 → 决定是否调用工具
  //    → 执行工具 → 结果回传 LLM
  //    → LLM 继续推理 → ... → 最终回答
  // ════════════════════════════════════════════
  mod('llm', 'processing', '调用 Claude，开始推理…');

  const client  = new Anthropic({ apiKey });
  const apiMsgs = [...session.history.slice(-8), { role: 'user', content: message }];
  let cursor    = apiMsgs;
  let finalText = '';
  const toolsUsed = [];

  for (let iter = 0; iter < 5; iter++) {  // 最多 5 轮工具调用
    const resp = await client.messages.create({
      model:    'claude-haiku-4-5-20251001',
      max_tokens: 1024,
      system:   sysPrompt,
      tools:    TOOL_DEFINITIONS,
      messages: cursor,
    });

    // LLM 直接回答，退出循环
    if (resp.stop_reason === 'end_turn') {
      finalText = resp.content.find(b => b.type === 'text')?.text ?? '';
      mod('llm', 'done',
        `生成完成，输入 ${resp.usage.input_tokens} / 输出 ${resp.usage.output_tokens} tokens`);
      break;
    }

    // LLM 决定调用工具（Function Call）
    if (resp.stop_reason === 'tool_use') {
      const toolBlocks = resp.content.filter(b => b.type === 'tool_use');
      mod('llm', 'done', `决策：调用工具 ${toolBlocks.map(b => b.name).join(', ')}`);

      const toolResults = [];

      for (const tb of toolBlocks) {

        // ── Function Call：LLM 生成结构化调用参数 ──
        mod('function_call', 'processing', `LLM 生成调用：${tb.name}()`);
        await wait(120);
        mod('function_call', 'done', `结构化参数：${JSON.stringify(tb.input)}`);

        // ── MCP + RAG（知识库检索场景）────────────
        if (tb.name === 'search_knowledge_base') {
          mod('mcp',    'processing', 'MCP 协议路由至知识库服务…');
          mod('rag',    'processing', '关键词检索知识库…');
          await wait(300);
          mod('mcp',    'done', '服务路由成功');
          mod('rag',    'done', '知识库检索完成，片段已召回');
          mod('skills', 'processing', '激活：知识整合技能');
          await wait(80);
          mod('skills', 'done', '技能就绪');
        }

        // ── Tools：实际执行工具逻辑 ────────────────
        mod('tools', 'processing', `执行 ${tb.name}…`);
        await wait(200);
        const result = executeTool(tb.name, tb.input, session, addPersistedMemory);
        mod('tools', 'done',
          tb.name === 'save_to_memory' ? '已写入持久化记忆 + 向量索引' : '执行成功，结果已返回');
        toolsUsed.push(tb.name);

        // ── Human-in-the-loop（记忆写入触发审核）────
        if (tb.name === 'save_to_memory') {
          mod('hitl', 'processing', '检测到记忆写入操作，触发审核流程…');
          await wait(350);
          mod('hitl', 'done', `低风险操作，自动批准。持久化总量：${persistedMem.length} 条`);
        }

        // ── SubAgent + Workflow（复杂任务）──────────
        if (isComplex && iter === 0) {
          mod('subagent',  'processing', '分配 SubAgent 处理子任务…');
          mod('workflow',  'processing', '启动工作流：收集 → 处理 → 整合');
          await wait(320);
          mod('subagent',  'done', 'SubAgent 完成，结果已汇总至主 Agent');
          mod('workflow',  'done', '工作流执行完毕');
        }

        toolResults.push({
          type:        'tool_result',
          tool_use_id: tb.id,
          content:     JSON.stringify(result),
        });
      }

      // 将工具结果追加到消息链，继续下一轮推理
      cursor = [
        ...cursor,
        { role: 'assistant', content: resp.content },
        { role: 'user',      content: toolResults  },
      ];
      mod('llm', 'processing', '整合工具结果，生成最终回答…');
    }
  }


  // ════════════════════════════════════════════
  //  Step 8 · Guardrails（输出护栏）
  // ════════════════════════════════════════════
  mod('guardrails', 'processing', '输出内容安全检查…');
  await wait(120);

  const outputSafety = checkOutput(finalText);
  if (!outputSafety.safe) {
    finalText = `抱歉，生成的内容触发了安全护栏（${outputSafety.reason}），已被过滤。`;
    mod('guardrails', 'blocked', outputSafety.reason);
  } else {
    mod('guardrails', 'done', '输出检查通过，无敏感内容');
  }


  // ════════════════════════════════════════════
  //  Step 9 · Memory 更新（自动提取会话内记忆）
  // ════════════════════════════════════════════
  Memory.autoExtractSession(message, session, persistedMem);


  // ════════════════════════════════════════════
  //  Step 10 · Evaluation（质量评估）
  // ════════════════════════════════════════════
  mod('evaluation', 'processing', '评估回答质量…');
  await wait(180);
  mod('evaluation', 'done', buildEvalReport(finalText, toolsUsed));


  // ════════════════════════════════════════════
  //  更新对话历史（短期记忆）
  // ════════════════════════════════════════════
  session.history.push({ role: 'user',      content: message   });
  session.history.push({ role: 'assistant', content: finalText });

  emit('text', { text: finalText });
  emit('done', {});
}

// ─────────────────────────────────────────────
//  启动
// ─────────────────────────────────────────────
const PORT = 3000;
app.listen(PORT, () => {
  console.log('\n✅  AI Agent 演示服务器已启动');
  console.log(`   浏览器打开: http://localhost:${PORT}`);
  console.log(`   各模块代码: ./modules/\n`);
});
