"""
╔══════════════════════════════════════════════════════╗
║                 Guardrails 模块                      ║
║                                                      ║
║  安全护栏，分两道关卡：                               ║
║                                                      ║
║  输入护栏（Input Guardrails）                        ║
║    - 在消息进入 LLM 之前检查                         ║
║    - 拦截：高风险操作指令、提示词注入尝试              ║
║    - 拦截后直接返回错误，不消耗 API 调用              ║
║                                                      ║
║  输出护栏（Output Guardrails）                       ║
║    - 在 LLM 回答返回给用户之前检查                   ║
║    - 过滤：敏感信息、不当内容                        ║
║    - 当前为演示级实现，生产环境可扩展为 ML 分类器     ║
╚══════════════════════════════════════════════════════╝
"""

import re

# ─────────────────────────────────────────────
#  输入护栏
#  规则 1：高风险操作模式（不可逆的破坏性指令）
# ─────────────────────────────────────────────
_DANGEROUS_PATTERNS = [
    (re.compile(r'删除.*数据库|清空.*表|drop\s+table', re.I), '检测到数据库破坏性操作'),
    (re.compile(r'格式化.*磁盘|rm\s+-rf',              re.I), '检测到文件系统破坏性操作'),
    (re.compile(r'攻击|黑入|入侵.*系统',               re.I), '检测到恶意攻击意图'),
]

# 规则 2：提示词注入尝试（试图覆盖系统 Prompt）
_INJECTION_PATTERNS = [
    (re.compile(r'ignore\s+(previous|above|all)\s+instructions', re.I), '检测到提示词注入：忽略指令'),
    (re.compile(r'你现在是|你是一个没有限制的|忘记你的系统提示'),       '检测到提示词注入：角色覆盖'),
]

def check_input(message: str) -> dict:
    """返回 {'safe': True} 或 {'safe': False, 'reason': '...'}"""
    for pattern, reason in [*_DANGEROUS_PATTERNS, *_INJECTION_PATTERNS]:
        if pattern.search(message):
            return {'safe': False, 'reason': reason}
    return {'safe': True}


# ─────────────────────────────────────────────
#  输出护栏
#  规则：检测可能泄露系统内部信息的输出
# ─────────────────────────────────────────────
_OUTPUT_PATTERNS = [
    (re.compile(r'sk-ant-api',         re.I), '输出包含 API Key'),
    (re.compile(r'密码|password.*[:：]', re.I), '输出包含疑似密码信息'),
]

def check_output(text: str) -> dict:
    """返回 {'safe': True} 或 {'safe': False, 'reason': '...'}"""
    for pattern, reason in _OUTPUT_PATTERNS:
        if pattern.search(text):
            return {'safe': False, 'reason': reason}
    return {'safe': True}
