/**
 * ╔══════════════════════════════════════════════════════╗
 * ║                 Guardrails 模块                      ║
 * ║                                                      ║
 * ║  安全护栏，分两道关卡：                               ║
 * ║                                                      ║
 * ║  输入护栏（Input Guardrails）                        ║
 * ║    - 在消息进入 LLM 之前检查                         ║
 * ║    - 拦截：高风险操作指令、提示词注入尝试              ║
 * ║    - 拦截后直接返回错误，不消耗 API 调用              ║
 * ║                                                      ║
 * ║  输出护栏（Output Guardrails）                       ║
 * ║    - 在 LLM 回答返回给用户之前检查                   ║
 * ║    - 过滤：敏感信息、不当内容                        ║
 * ║    - 当前为演示级实现，生产环境可扩展为 ML 分类器     ║
 * ╚══════════════════════════════════════════════════════╝
 */

'use strict';

// ─────────────────────────────────────────────
//  输入护栏
//  返回 { safe: true } 或 { safe: false, reason: '...' }
// ─────────────────────────────────────────────

// 规则 1：高风险操作模式（不可逆的破坏性指令）
const DANGEROUS_PATTERNS = [
  { pattern: /删除.*数据库|清空.*表|drop\s+table/i,  reason: '检测到数据库破坏性操作' },
  { pattern: /格式化.*磁盘|rm\s+-rf/i,               reason: '检测到文件系统破坏性操作' },
  { pattern: /攻击|黑入|入侵.*系统/i,                reason: '检测到恶意攻击意图' },
];

// 规则 2：提示词注入尝试（试图覆盖系统 Prompt）
const INJECTION_PATTERNS = [
  { pattern: /ignore\s+(previous|above|all)\s+instructions/i, reason: '检测到提示词注入：忽略指令' },
  { pattern: /你现在是|你是一个没有限制的|忘记你的系统提示/,   reason: '检测到提示词注入：角色覆盖' },
];

function checkInput(message) {
  for (const { pattern, reason } of [...DANGEROUS_PATTERNS, ...INJECTION_PATTERNS]) {
    if (pattern.test(message)) {
      return { safe: false, reason };
    }
  }
  return { safe: true };
}

// ─────────────────────────────────────────────
//  输出护栏
//  返回 { safe: true } 或 { safe: false, reason: '...' }
// ─────────────────────────────────────────────

// 规则：检测可能泄露系统内部信息的输出
const OUTPUT_PATTERNS = [
  { pattern: /sk-ant-api/i,        reason: '输出包含 API Key' },
  { pattern: /密码|password.*[:：]/i, reason: '输出包含疑似密码信息' },
];

function checkOutput(text) {
  for (const { pattern, reason } of OUTPUT_PATTERNS) {
    if (pattern.test(text)) {
      return { safe: false, reason };
    }
  }
  return { safe: true };
}

module.exports = { checkInput, checkOutput };
