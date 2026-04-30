"""
╔══════════════════════════════════════════════════════╗
║               Human-in-the-Loop 模块                 ║
║                                                      ║
║  在高风险操作前暂停 Pipeline，等待人工审核             ║
║                                                      ║
║  工作机制：                                           ║
║    1. Pipeline 调用 request_approval()               ║
║    2. 生成审核条目，写入等待队列                       ║
║    3. Pipeline 通过 asyncio.Event 挂起等待            ║
║    4. 用户通过 POST /api/hitl/{id}/approve 或 reject  ║
║    5. Event 触发，Pipeline 收到决定后继续执行          ║
║    6. 超时后自动批准（默认 60 秒）                    ║
║                                                      ║
║  与 Guardrails 的区别：                              ║
║    Guardrails  → 机器自动判断，立刻拦截或放行         ║
║    HITL        → 将决策权交还给人，异步等待           ║
╚══════════════════════════════════════════════════════╝
"""

import asyncio
import uuid
from datetime import datetime


class HITLQueue:
    """
    人工审核队列

    每个等待中的审核条目包含：
      id        - 唯一标识（前端用来 approve/reject）
      operation - 操作名称（工具名）
      details   - 操作参数
      status    - pending / approved / rejected / auto-approved
      at        - 创建时间
    """

    def __init__(self, auto_approve_timeout: float = 60.0):
        self.auto_approve_timeout = auto_approve_timeout
        self._queue:     dict[str, dict]          = {}
        self._events:    dict[str, asyncio.Event] = {}
        self._decisions: dict[str, bool]          = {}

    # ── 请求审核（两步：先建条目拿 ID，再等待）────
    def create_approval(self, operation: str, details: dict) -> str:
        """创建审核条目，返回 approval_id（不等待）"""
        approval_id = uuid.uuid4().hex[:8]
        self._queue[approval_id] = {
            'id':        approval_id,
            'operation': operation,
            'details':   details,
            'status':    'pending',
            'at':        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        self._events[approval_id] = asyncio.Event()
        return approval_id

    async def wait_for_decision(self, approval_id: str) -> bool:
        """等待人工决定（或超时自动批准）"""
        event = self._events.get(approval_id)
        if event is None:
            return True
        try:
            await asyncio.wait_for(event.wait(), timeout=self.auto_approve_timeout)
            decision = self._decisions.get(approval_id, True)
            self._queue[approval_id]['status'] = 'approved' if decision else 'rejected'
            return decision
        except asyncio.TimeoutError:
            self._queue[approval_id]['status'] = 'auto-approved'
            return True
        finally:
            self._events.pop(approval_id, None)
            self._decisions.pop(approval_id, None)

    async def request_approval(self, operation: str, details: dict) -> bool:
        """一步完成：建条目 + 等待（兼容旧调用）"""
        aid = self.create_approval(operation, details)
        return await self.wait_for_decision(aid)

    # ── 审核决定（通过 HTTP API 触发）────────────────
    def approve(self, approval_id: str) -> bool:
        if approval_id not in self._events:
            return False
        self._decisions[approval_id] = True
        self._events[approval_id].set()
        return True

    def reject(self, approval_id: str) -> bool:
        if approval_id not in self._events:
            return False
        self._decisions[approval_id] = False
        self._events[approval_id].set()
        return True

    # ── 查询 ─────────────────────────────────────────
    def get_pending(self) -> list[dict]:
        return [v for v in self._queue.values() if v['status'] == 'pending']

    def get_all(self, last_n: int = 20) -> list[dict]:
        items = list(self._queue.values())
        return items[-last_n:]
