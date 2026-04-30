"""
╔══════════════════════════════════════════════════════╗
║                  MCP Server 模块                     ║
║                                                      ║
║  Model Context Protocol 服务器                       ║
║                                                      ║
║  实现 MCP JSON-RPC 协议的核心部分：                   ║
║    - 工具注册（register）                            ║
║    - 工具发现（list_tools → GET /mcp/tools）         ║
║    - 工具调用（call_tool → POST /mcp/tools/call）    ║
║                                                      ║
║  与直接调用 execute_tool 的区别：                     ║
║    直接调用  → 函数调用，无协议层                     ║
║    MCP 调用  → 标准协议路由，可替换为任意 MCP Server  ║
║                                                      ║
║  真实 MCP 是独立进程（stdio/HTTP），这里为演示将      ║
║  Server 内嵌在同一进程，协议格式完全遵循 MCP 规范     ║
╚══════════════════════════════════════════════════════╝
"""

import json
from datetime import datetime
from typing import Callable


class MCPServer:
    """
    MCP Server：工具注册表 + 协议路由层

    工具必须先 register() 才能被 call_tool() 调用。
    server.py 启动时注册所有工具，之后所有工具调用经此路由。
    """

    def __init__(self, name: str = 'agent-mcp-server', version: str = '1.0.0'):
        self.name    = name
        self.version = version
        self._tools: dict[str, dict] = {}   # name → {description, inputSchema, handler}
        self._call_log: list[dict]   = []   # 调用历史（用于审计）

    # ── 工具注册 ──────────────────────────────────
    def register(self, name: str, description: str,
                 input_schema: dict, handler: Callable) -> None:
        """注册一个工具到 MCP Server"""
        self._tools[name] = {
            'name':        name,
            'description': description,
            'inputSchema': input_schema,
            'handler':     handler,
        }

    # ── 协议：发现工具（MCP tools/list）──────────
    def list_tools(self) -> dict:
        """
        返回格式遵循 MCP 规范：
        { "tools": [ { "name", "description", "inputSchema" }, ... ] }
        """
        return {
            'server':  self.name,
            'version': self.version,
            'tools': [
                {'name': t['name'], 'description': t['description'],
                 'inputSchema': t['inputSchema']}
                for t in self._tools.values()
            ],
        }

    # ── 协议：调用工具（MCP tools/call）──────────
    def call_tool(self, name: str, arguments: dict, executor=None) -> dict:
        """
        MCP tools/call 协议格式：
          请求：{ "name": "...", "arguments": {...} }
          成功：{ "content": [...], "_result": <raw> }
          失败：{ "isError": true, "content": [...] }

        executor（可选）：
          由 pipeline 传入的 lambda，捕获当前请求的 session 上下文。
          优先使用 executor；若无 executor 则使用注册时的 handler。
        """
        if name not in self._tools:
            return {
                'isError': True,
                'content': [{'type': 'text', 'text': f'工具未注册: {name}'}],
            }

        try:
            if executor is not None:
                raw = executor(name, arguments)
            else:
                handler = self._tools[name].get('handler')
                if handler is None:
                    return {'isError': True,
                            'content': [{'type': 'text', 'text': '未提供执行器且无默认 handler'}]}
                raw = handler(arguments)

            self._call_log.append({
                'tool': name, 'at': datetime.utcnow().isoformat() + 'Z',
                'success': True,
            })
            return {
                'content': [{'type': 'text',
                             'text': json.dumps(raw, ensure_ascii=False)}],
                '_result': raw,
            }
        except Exception as e:
            self._call_log.append({
                'tool': name, 'at': datetime.utcnow().isoformat() + 'Z',
                'success': False, 'error': str(e),
            })
            return {
                'isError': True,
                'content': [{'type': 'text', 'text': f'工具执行失败: {e}'}],
            }

    # ── 辅助 ──────────────────────────────────────
    def tool_count(self) -> int:
        return len(self._tools)

    def call_history(self, last_n: int = 20) -> list:
        return self._call_log[-last_n:]
