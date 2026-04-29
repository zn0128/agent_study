"""
╔══════════════════════════════════════════════════════╗
║              Tools + Function Call 模块              ║
║                                                      ║
║  两个概念，一个文件，因为它们紧密配合：               ║
║                                                      ║
║  Function Call（函数调用）                           ║
║    - LLM 决定调用哪个工具、生成结构化参数             ║
║    - 这是 LLM 侧的能力（输出 JSON 而非文本）          ║
║    - 本文件定义工具的"接口描述"（给 LLM 看的说明书）  ║
║                                                      ║
║  Tools（工具执行）                                   ║
║    - Agent 框架捕获 LLM 的 JSON 指令后实际执行        ║
║    - 这是服务器侧的能力（真实计算、文件读写等）        ║
║    - 本文件实现各工具的具体逻辑                       ║
║                                                      ║
║  调用链：                                            ║
║    用户消息 → LLM 推理 → 输出 Function Call JSON      ║
║    → execute_tool() 执行 → 结果回传 LLM → 最终回答   ║
╚══════════════════════════════════════════════════════╝
"""

import re
from datetime import datetime
from typing import Callable

from .rag        import search_knowledge_base
from .news       import fetch_news

# ─────────────────────────────────────────────
#  工具接口描述（传给 Claude API 的 tools 参数）
#
#  这是 Function Call 的核心：LLM 读取这些描述，
#  自主决定什么时候调用哪个工具、填什么参数。
#  description 写得越清晰，LLM 决策越准确。
# ─────────────────────────────────────────────
TOOL_DEFINITIONS = [
    {
        'name':        'search_knowledge_base',
        'description': '搜索 AI Agent 知识库，获取关于 Agent 各模块（Memory、RAG、MCP、Planning、Guardrails 等）的详细解释。用户询问 AI Agent 相关概念时必须使用此工具。',
        'input_schema': {
            'type':       'object',
            'properties': {'query': {'type': 'string', 'description': '搜索关键词或问题'}},
            'required':   ['query'],
        },
    },
    {
        'name':        'calculate',
        'description': '执行数学计算，支持加减乘除、幂运算、括号等。',
        'input_schema': {
            'type':       'object',
            'properties': {'expression': {'type': 'string', 'description': '数学表达式，如 "2 + 3 * 4"'}},
            'required':   ['expression'],
        },
    },
    {
        'name':        'get_datetime',
        'description': '获取当前日期和时间。',
        'input_schema': {'type': 'object', 'properties': {}},
    },
    {
        'name':        'get_news',
        'description': '获取实时新闻头条。用户询问今日新闻、最新资讯、某类话题动态时使用。',
        'input_schema': {
            'type':       'object',
            'properties': {
                'topic': {
                    'type':        'string',
                    'enum':        ['general', 'technology', 'business', 'world', 'science'],
                    'description': '新闻类别：general 综合 / technology 科技 / business 财经 / world 国际 / science 科学',
                },
                'count': {
                    'type':        'integer',
                    'description': '返回条数，默认 5，最多 10',
                },
            },
        },
    },
    {
        'name':        'add_to_memory_bank',
        'description': '将一篇知识文档（文章、笔记、代码、会议记录等较长内容）存入 Memory Bank。与 save_to_memory 的区别：save_to_memory 存短小的个人事实；add_to_memory_bank 存可检索的知识文档。',
        'input_schema': {
            'type':       'object',
            'properties': {
                'title':   {'type': 'string', 'description': '文档标题'},
                'content': {'type': 'string', 'description': '文档正文内容'},
            },
            'required': ['title', 'content'],
        },
    },
    {
        'name':        'search_memory_bank',
        'description': '在 Memory Bank 中语义检索知识文档。用户询问之前存入的文档内容时使用。',
        'input_schema': {
            'type':       'object',
            'properties': {'query': {'type': 'string', 'description': '检索关键词或问题'}},
            'required':   ['query'],
        },
    },
    {
        'name':        'save_to_memory',
        'description': '将用户提供的重要信息或偏好保存到长期持久化记忆，跨会话有效。用户明确要求记住某事时使用。',
        'input_schema': {
            'type':       'object',
            'properties': {
                'category': {'type': 'string', 'description': '记忆类别，如 "用户偏好"、"项目信息"'},
                'content':  {'type': 'string', 'description': '要保存的具体内容'},
            },
            'required': ['category', 'content'],
        },
    },
]


# ─────────────────────────────────────────────
#  工具执行函数（Tools 层）
#
#  参数：
#    name        - 工具名称（来自 LLM 的 Function Call）
#    input       - 工具参数（LLM 自动生成的结构化 JSON）
#    session     - 当前会话（某些工具需要操作会话数据）
#    add_memory  - 添加持久化记忆的回调函数（由 memory 模块提供）
#    memory_bank - MemoryBank 实例（由 server.py 提供）
# ─────────────────────────────────────────────
def execute_tool(name: str, inp: dict, session: dict, add_memory: Callable, memory_bank=None) -> dict:

    # ── 工具 1：知识库检索（配合 RAG 模块）──────
    if name == 'search_knowledge_base':
        return search_knowledge_base(inp['query'])

    # ── 工具 2：实时新闻 ────────────────────────
    elif name == 'get_news':
        topic = inp.get('topic', 'general')
        count = min(int(inp.get('count', 5)), 10)
        return fetch_news(topic, count)

    # ── 工具 3：Memory Bank 写入 ────────────────
    elif name == 'add_to_memory_bank':
        if memory_bank is None:
            return {'error': 'Memory Bank 未初始化'}
        doc = memory_bank.add(
            title   = inp['title'],
            content = inp['content'],
            source  = 'agent',
        )
        return {'success': True, 'id': doc['id'], 'title': doc['title'],
                'bank_size': memory_bank.size()}

    # ── 工具 4：Memory Bank 检索 ────────────────
    elif name == 'search_memory_bank':
        if memory_bank is None:
            return {'error': 'Memory Bank 未初始化'}
        hits = memory_bank.search(inp['query'], top_k=3)
        if not hits:
            return {'found': False, 'message': 'Memory Bank 中无匹配文档'}
        return {'found': True, 'count': len(hits),
                'results': [{'title': h['title'], 'content': h['content'],
                             'score': h['score']} for h in hits]}

    # ── 工具 5：数学计算 ────────────────────────
    elif name == 'calculate':
        expression = inp.get('expression', '')
        # 安全过滤：只允许数字和基础运算符，防止代码注入
        safe = re.sub(r'[^0-9+\-*/.()% ]', '', expression)
        if not safe.strip():
            return {'error': '无效表达式'}
        try:
            result = eval(safe, {'__builtins__': {}}, {})  # noqa: S307
            return {'expression': expression, 'result': round(float(result), 10)}
        except Exception as e:
            return {'error': f'计算失败: {e}'}

    # ── 工具 3：获取当前时间 ────────────────────
    elif name == 'get_datetime':
        now = datetime.now()
        weekdays = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
        return {
            'date': f'{now.year}年{now.month}月{now.day}日 {weekdays[now.weekday()]}',
            'time': now.strftime('%H:%M:%S'),
        }

    # ── 工具 4：保存记忆（联动 Memory 模块）───────
    elif name == 'save_to_memory':
        entry = {
            'id':       str(int(datetime.now().timestamp() * 1000)),
            'category': inp['category'],
            'content':  inp['content'],
            'at':       datetime.utcnow().isoformat() + 'Z',
            'source':   'explicit',  # 区别于 auto_extract 的自动提取
        }
        # 写入持久化存储 + 向量索引（由 memory 模块处理）
        add_memory(entry)
        # 同步到会话内事实，本次会话立即可用
        session['session_facts'].append(entry)
        return {'success': True, 'saved': f'[{entry["category"]}] {entry["content"]}', 'persisted': True}

    else:
        return {'error': f'未知工具: {name}'}
