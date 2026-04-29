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
║  具体的模块逻辑都在 modules/ 目录下：                 ║
║    memory.py      三层记忆体系                       ║
║    rag.py         知识库检索                         ║
║    guardrails.py  安全护栏                           ║
║    tools.py       工具定义与执行                     ║
║    planning.py    任务规划                           ║
║    prompt.py      提示词构建                         ║
║    evaluation.py  质量评估                           ║
╚══════════════════════════════════════════════════════╝
"""

import asyncio
import json
import os
import sys

# Windows 终端默认 cp1252，强制 UTF-8 输出
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

from modules.memory      import (VectorMemory, load_persisted, save_persisted,
                                  get_session, retrieve_all, auto_extract_session)
from modules.guardrails  import check_input, check_output
from modules.tools       import TOOL_DEFINITIONS, execute_tool
from modules.planning    import analyze_task
from modules.prompt      import build_system_prompt
from modules.evaluation  import build_eval_report
from modules.memory_bank import MemoryBank

# ─────────────────────────────────────────────
#  初始化
# ─────────────────────────────────────────────
app = FastAPI()

# 启动时加载持久化记忆，建立向量索引
persisted_mem: list[dict] = load_persisted()
vector_mem = VectorMemory()
vector_mem.rebuild(persisted_mem)
print(f'[Memory] 已加载 {len(persisted_mem)} 条持久化记忆')

memory_bank = MemoryBank()
print(f'[MemoryBank] 已加载 {memory_bank.size()} 篇文档')

# 向 Memory 模块提供"添加持久化记忆"的回调
# （避免 memory.py 直接依赖 persisted_mem 变量）
def add_persisted_memory(entry: dict):
    persisted_mem.append(entry)
    vector_mem.add(entry)
    save_persisted(persisted_mem)


# ─────────────────────────────────────────────
#  API 路由
# ─────────────────────────────────────────────

# 查看所有持久化记忆（调试用）
@app.get('/api/memories')
def get_memories():
    return {'count': len(persisted_mem), 'memories': persisted_mem[-50:]}

@app.get('/api/memory-bank')
def get_memory_bank():
    return {'count': memory_bank.size(), 'documents': memory_bank.list_all()}


# 主对话端点：POST body → SSE 流式响应
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
#  Pipeline：按顺序调用各模块（异步生成器，产出 SSE 事件）
#
#  每个步骤 yield SSE 事件，前端收到后实时更新模块可视化面板。
# ─────────────────────────────────────────────
async def run_pipeline(message: str, session_id: str, api_key: str) -> AsyncGenerator[str, None]:
    session = get_session(session_id)

    def sse(event: str, data: dict) -> str:
        return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'

    def mod(id: str, status: str, log: str) -> str:
        return sse('module', {'id': id, 'status': status, 'log': log})

    try:
        # ════════════════════════════════════════════
        #  常驻模块（始终运行）
        # ════════════════════════════════════════════
        yield mod('langchain', 'active', '框架初始化，加载运行时组件')
        yield mod('agent',     'active', '主 Agent 启动，接收用户输入')


        # ════════════════════════════════════════════
        #  Step 1 · Guardrails（输入护栏）
        #  最先执行，拦截后直接返回，不消耗 API 调用
        # ════════════════════════════════════════════
        yield mod('guardrails', 'processing', '输入安全检查中…')
        await asyncio.sleep(0.12)

        input_safety = check_input(message)
        if not input_safety['safe']:
            yield mod('guardrails', 'blocked', input_safety['reason'])
            yield sse('text', {'text': f'⚠️ 请求已被安全护栏拦截：{input_safety["reason"]}'})
            yield sse('done', {})
            return
        yield mod('guardrails', 'done', '安全检查通过，无注入或高风险指令')


        # ════════════════════════════════════════════
        #  Step 2 · Memory（三层记忆检索）
        #  在构建 Prompt 之前检索，结果注入 Prompt
        # ════════════════════════════════════════════
        yield mod('memory', 'processing', '[1/3] 会话内记忆：检查本次会话的临时事实…')
        await asyncio.sleep(0.08)

        yield mod('memory', 'processing',
                  f'[1/3] 会话内记忆：{len(session["session_facts"])} 条事实')
        await asyncio.sleep(0.08)

        yield mod('memory', 'processing',
                  f'[2/3] 向量检索：对 {vector_mem.size()} 条持久化记忆计算 TF-IDF 余弦相似度…')
        await asyncio.sleep(0.22)

        result      = retrieve_all(message, session, vector_mem)
        session_facts = result['session_facts']
        vector_hits   = result['vector_hits']

        yield mod('memory', 'processing',
                  f'[3/3] 注入上下文：会话内 {len(session_facts)} 条 + 向量命中 {len(vector_hits)} 条')
        await asyncio.sleep(0.10)

        if vector_hits:
            mem_log = f'向量命中 {len(vector_hits)} 条（最高相关度 {vector_hits[0]["score"]:.2f}），会话内 {len(session_facts)} 条'
        elif session_facts:
            mem_log = f'仅会话内记忆 {len(session_facts)} 条，持久化库无语义命中'
        else:
            mem_log = f'无相关记忆（持久化库共 {len(persisted_mem)} 条）'
        yield mod('memory', 'done', mem_log)


        # ════════════════════════════════════════════
        #  Step 3 · Prompt（构建系统提示词）
        #  将记忆检索结果注入 Prompt
        # ════════════════════════════════════════════
        yield mod('prompt', 'processing', '构建系统提示词（含记忆注入）…')
        sys_prompt = build_system_prompt(session_facts, vector_hits)
        await asyncio.sleep(0.08)
        yield mod('prompt', 'done',
                  f'提示词就绪（{len(sys_prompt)} 字符），已注入 {len(session_facts) + len(vector_hits)} 条记忆')


        # ════════════════════════════════════════════
        #  Step 4 · Context（构建上下文窗口）
        # ════════════════════════════════════════════
        yield mod('context', 'processing', '构建上下文窗口…')
        await asyncio.sleep(0.08)
        hist_len = len(session['history'])
        est_pct  = min(
            round((hist_len * 120 + len(message) + len(sys_prompt)) / 200000 * 100),
            99
        )
        yield mod('context', 'done', f'窗口占用约 {est_pct}%，含 {hist_len} 轮历史对话')


        # ════════════════════════════════════════════
        #  Step 5 · Planning（任务规划）
        # ════════════════════════════════════════════
        yield mod('planning', 'processing', '分析任务类型，制定处理策略…')
        await asyncio.sleep(0.24)
        plan = analyze_task(message)
        is_complex = plan['is_complex']
        yield mod('planning', 'done', plan['mode_desc'])


        # ════════════════════════════════════════════
        #  Step 6 · Orchestrator（复杂任务编排）
        #  仅在 Planning 判定为复杂任务时激活
        # ════════════════════════════════════════════
        if is_complex:
            yield mod('orchestrator', 'processing', '任务编排，分配执行单元…')
            await asyncio.sleep(0.16)
            yield mod('orchestrator', 'done', '编排就绪，主 Agent 执行，按需调度 SubAgent')


        # ════════════════════════════════════════════
        #  Step 7 · LLM + 工具调用循环（ReAct 核心）
        #
        #  这是 Agent 的核心循环：
        #    LLM 推理 → 决定是否调用工具
        #    → 执行工具 → 结果回传 LLM
        #    → LLM 继续推理 → ... → 最终回答
        # ════════════════════════════════════════════
        yield mod('llm', 'processing', '调用 Claude，开始推理…')

        # 公司网络有 SSL 拦截（自签名证书），关闭 TLS 验证
        client   = anthropic.AsyncAnthropic(
            api_key=api_key,
            http_client=httpx.AsyncClient(verify=False),
        )
        api_msgs = [*session['history'][-8:], {'role': 'user', 'content': message}]
        cursor   = api_msgs
        final_text = ''
        tools_used: list[str] = []

        for iter_i in range(5):  # 最多 5 轮工具调用
            resp = await client.messages.create(
                model      = 'claude-haiku-4-5-20251001',
                max_tokens = 1024,
                system     = sys_prompt,
                tools      = TOOL_DEFINITIONS,
                messages   = cursor,
            )

            # LLM 直接回答，退出循环
            if resp.stop_reason == 'end_turn':
                final_text = next(
                    (b.text for b in resp.content if b.type == 'text'), ''
                )
                yield mod('llm', 'done',
                          f'生成完成，输入 {resp.usage.input_tokens} / 输出 {resp.usage.output_tokens} tokens')
                break

            # LLM 决定调用工具（Function Call）
            if resp.stop_reason == 'tool_use':
                tool_blocks = [b for b in resp.content if b.type == 'tool_use']
                yield mod('llm', 'done',
                          f'决策：调用工具 {", ".join(b.name for b in tool_blocks)}')

                tool_results = []

                for tb in tool_blocks:

                    # ── Function Call：LLM 生成结构化调用参数 ──
                    yield mod('function_call', 'processing', f'LLM 生成调用：{tb.name}()')
                    await asyncio.sleep(0.12)
                    yield mod('function_call', 'done',
                              f'结构化参数：{json.dumps(tb.input, ensure_ascii=False)}')

                    # ── MCP + RAG（知识库检索场景）────────────
                    if tb.name == 'search_knowledge_base':
                        yield mod('mcp',    'processing', 'MCP 协议路由至知识库服务…')
                        yield mod('rag',    'processing', '关键词检索知识库…')
                        await asyncio.sleep(0.30)
                        yield mod('mcp',    'done', '服务路由成功')
                        yield mod('rag',    'done', '知识库检索完成，片段已召回')
                        yield mod('skills', 'processing', '激活：知识整合技能')
                        await asyncio.sleep(0.08)
                        yield mod('skills', 'done', '技能就绪')

                    # ── Tools：实际执行工具逻辑 ────────────────
                    yield mod('tools', 'processing', f'执行 {tb.name}…')
                    await asyncio.sleep(0.20)
                    result_data = execute_tool(tb.name, tb.input, session, add_persisted_memory, memory_bank)
                    yield mod('tools', 'done',
                              '已写入持久化记忆 + 向量索引' if tb.name == 'save_to_memory'
                              else '执行成功，结果已返回')
                    tools_used.append(tb.name)

                    # ── Human-in-the-loop（记忆写入触发审核）────
                    if tb.name == 'save_to_memory':
                        yield mod('hitl', 'processing', '检测到记忆写入操作，触发审核流程…')
                        await asyncio.sleep(0.35)
                        yield mod('hitl', 'done',
                                  f'低风险操作，自动批准。持久化总量：{len(persisted_mem)} 条')

                    # ── SubAgent + Workflow（复杂任务）──────────
                    if is_complex and iter_i == 0:
                        yield mod('subagent',  'processing', '分配 SubAgent 处理子任务…')
                        yield mod('workflow',  'processing', '启动工作流：收集 → 处理 → 整合')
                        await asyncio.sleep(0.32)
                        yield mod('subagent',  'done', 'SubAgent 完成，结果已汇总至主 Agent')
                        yield mod('workflow',  'done', '工作流执行完毕')

                    tool_results.append({
                        'type':        'tool_result',
                        'tool_use_id': tb.id,
                        'content':     json.dumps(result_data, ensure_ascii=False),
                    })

                # 将工具结果追加到消息链，继续下一轮推理
                cursor = [
                    *cursor,
                    {'role': 'assistant', 'content': [b.model_dump() for b in resp.content]},
                    {'role': 'user',      'content': tool_results},
                ]
                yield mod('llm', 'processing', '整合工具结果，生成最终回答…')


        # ════════════════════════════════════════════
        #  Step 8 · Guardrails（输出护栏）
        # ════════════════════════════════════════════
        yield mod('guardrails', 'processing', '输出内容安全检查…')
        await asyncio.sleep(0.12)

        output_safety = check_output(final_text)
        if not output_safety['safe']:
            final_text = f'抱歉，生成的内容触发了安全护栏（{output_safety["reason"]}），已被过滤。'
            yield mod('guardrails', 'blocked', output_safety['reason'])
        else:
            yield mod('guardrails', 'done', '输出检查通过，无敏感内容')


        # ════════════════════════════════════════════
        #  Step 9 · Memory 更新（自动提取会话内记忆）
        # ════════════════════════════════════════════
        auto_extract_session(message, session, persisted_mem)


        # ════════════════════════════════════════════
        #  Step 10 · Evaluation（质量评估）
        # ════════════════════════════════════════════
        yield mod('evaluation', 'processing', '评估回答质量…')
        await asyncio.sleep(0.18)
        yield mod('evaluation', 'done', build_eval_report(final_text, tools_used))


        # ════════════════════════════════════════════
        #  更新对话历史（短期记忆）
        # ════════════════════════════════════════════
        session['history'].append({'role': 'user',      'content': message})
        session['history'].append({'role': 'assistant', 'content': final_text})

        yield sse('text', {'text': final_text})
        yield sse('done', {})

    except Exception as e:
        print(f'[Pipeline Error] {e}')
        yield sse('error', {'message': str(e) or 'API 调用失败，请检查 API Key'})


# ─────────────────────────────────────────────
#  静态文件（前端 index.html）
#  必须在 API 路由之后挂载，避免覆盖 /api/* 路径
# ─────────────────────────────────────────────
app.mount('/', StaticFiles(directory=os.path.dirname(__file__), html=True), name='static')


# ─────────────────────────────────────────────
#  启动
# ─────────────────────────────────────────────
if __name__ == '__main__':
    import uvicorn
    print('\nAI Agent 演示服务器已启动（Python 版本）')
    print('   浏览器打开: http://localhost:3000')
    print('   各模块代码: ./modules/\n')
    uvicorn.run('server:app', host='0.0.0.0', port=3000, reload=False)
