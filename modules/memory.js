/**
 * ╔══════════════════════════════════════════════════════╗
 * ║                   Memory 模块                        ║
 * ║                                                      ║
 * ║  负责 Agent 的三层记忆体系：                           ║
 * ║                                                      ║
 * ║  Layer 1 · 会话内记忆 (Session Memory)               ║
 * ║    - 存在于当前会话的 RAM 中                          ║
 * ║    - 会话结束即失效                                   ║
 * ║    - 来源：本轮对话自动提取的临时事实                  ║
 * ║                                                      ║
 * ║  Layer 2 · 持久化记忆 (Persistent Memory)            ║
 * ║    - 写入磁盘 memories.json                          ║
 * ║    - 服务器重启后依然存在                             ║
 * ║    - 来源：用户明确要求 save_to_memory 时写入         ║
 * ║                                                      ║
 * ║  Layer 3 · 向量语义检索 (Vector Memory)              ║
 * ║    - 基于 TF-IDF 余弦相似度的语义匹配                 ║
 * ║    - 对持久化记忆建立向量索引                         ║
 * ║    - 每次对话按相关度召回最匹配的记忆                  ║
 * ╚══════════════════════════════════════════════════════╝
 */

'use strict';

const fs   = require('fs');
const path = require('path');

// ─────────────────────────────────────────────
//  Layer 3：TF-IDF 向量引擎
//
//  原理：
//    TF  (词频)     = 某词在文档中出现的频率
//    IDF (逆文档频率) = log((总文档数+1) / (含该词的文档数+1)) + 1
//    TF-IDF 向量     = 每个词的 TF × IDF 权重
//    余弦相似度       = 两个向量的点积 / (模长之积)
//
//  为什么用 TF-IDF 而不是关键词匹配？
//    关键词匹配："我叫张三" vs "你叫什么" → 无公共词 → 匹配失败
//    TF-IDF 字符分词："叫" 同时出现在两句 → 有相似度
// ─────────────────────────────────────────────
class VectorMemory {
  constructor() {
    this.docs = [];  // 所有文档（含向量）
    this.idf  = {};  // 每个 token 的 IDF 权重
  }

  // 分词器：英文按空格切词；中文拆成单字 + 相邻双字 bigram
  // 例："用户叫张三" → ['用','户','叫','张','三','用户','户叫','叫张','张三']
  _tokenize(text) {
    const tokens = [];
    const lower  = text.toLowerCase();
    const parts  = lower.split(/[^\w\u4e00-\u9fff]+/).filter(Boolean);

    for (const part of parts) {
      if (/[\u4e00-\u9fff]/.test(part)) {
        // 中文单字
        for (const ch of part) {
          if (/[\u4e00-\u9fff]/.test(ch)) tokens.push(ch);
        }
        // 中文 bigram（增强上下文语义）
        for (let i = 0; i < part.length - 1; i++) {
          if (/[\u4e00-\u9fff]/.test(part[i]) && /[\u4e00-\u9fff]/.test(part[i + 1])) {
            tokens.push(part[i] + part[i + 1]);
          }
        }
      } else if (part.length > 0) {
        tokens.push(part); // 英文整词
      }
    }
    return tokens;
  }

  // 重建全量 IDF 并刷新所有文档的 TF-IDF 向量
  // 每次新增文档后调用，因为新文档会改变 IDF 值
  _rebuild() {
    const N  = this.docs.length;
    const df = {};  // 每个 token 出现在多少篇文档中

    for (const doc of this.docs) {
      for (const token of new Set(doc.tokens)) {
        df[token] = (df[token] || 0) + 1;
      }
    }

    // IDF = log((N+1)/(df+1)) + 1，加 1 防止除零，稀有词权重更高
    this.idf = {};
    for (const [token, count] of Object.entries(df)) {
      this.idf[token] = Math.log((N + 1) / (count + 1)) + 1;
    }

    // 更新每篇文档的 TF-IDF 向量
    for (const doc of this.docs) {
      doc.vec = {};
      for (const [token, freq] of Object.entries(doc.tf)) {
        doc.vec[token] = (freq / doc.tokens.length) * (this.idf[token] || 1);
      }
    }
  }

  // 添加一条记忆到向量索引
  add(entry) {
    const text   = `${entry.category || ''} ${entry.content || ''}`;
    const tokens = this._tokenize(text);
    const tf     = {};
    for (const token of tokens) tf[token] = (tf[token] || 0) + 1;

    this.docs.push({ ...entry, tokens, tf, vec: null });
    this._rebuild();
  }

  // 余弦相似度计算
  _cosine(vecA, vecB) {
    let dot = 0, normA = 0, normB = 0;
    const allKeys = new Set([...Object.keys(vecA), ...Object.keys(vecB)]);

    for (const key of allKeys) {
      const a = vecA[key] || 0;
      const b = vecB[key] || 0;
      dot   += a * b;
      normA += a * a;
      normB += b * b;
    }

    return normA && normB ? dot / Math.sqrt(normA * normB) : 0;
  }

  // 语义搜索：返回与 query 最相似的 topK 条记忆
  search(query, topK = 4, threshold = 0.05) {
    if (!this.docs.length) return [];

    // 构建 query 的 TF-IDF 向量
    const qTokens = this._tokenize(query);
    const qTF     = {};
    for (const t of qTokens) qTF[t] = (qTF[t] || 0) + 1;

    const qVec = {};
    for (const [t, freq] of Object.entries(qTF)) {
      qVec[t] = (freq / qTokens.length) * (this.idf[t] || 1);
    }

    // 对所有文档计算相似度，过滤、排序、截取
    return this.docs
      .map(doc => ({ ...doc, score: this._cosine(qVec, doc.vec || {}) }))
      .filter(doc => doc.score > threshold)
      .sort((a, b) => b.score - a.score)
      .slice(0, topK);
  }

  size()  { return this.docs.length; }
  clear() { this.docs = []; this.idf = {}; }

  // 从持久化数组重建整个索引（服务器启动时调用）
  rebuild(entries) {
    this.clear();
    entries.forEach(e => this.add(e));
  }
}

// ─────────────────────────────────────────────
//  Layer 2：持久化存储（磁盘文件）
// ─────────────────────────────────────────────
const MEMORY_FILE = path.join(__dirname, '..', 'memories.json');

// 启动时从磁盘加载
function loadPersisted() {
  try {
    if (fs.existsSync(MEMORY_FILE)) {
      return JSON.parse(fs.readFileSync(MEMORY_FILE, 'utf-8'));
    }
  } catch (e) {
    console.error('[Memory] 加载持久化记忆失败:', e.message);
  }
  return [];
}

// 每次新增记忆后写入磁盘
function savePersisted(persistedMem) {
  try {
    fs.writeFileSync(MEMORY_FILE, JSON.stringify(persistedMem, null, 2));
  } catch (e) {
    console.error('[Memory] 保存持久化记忆失败:', e.message);
  }
}

// ─────────────────────────────────────────────
//  Layer 1：会话存储（RAM）
// ─────────────────────────────────────────────
const sessions = new Map();  // sessionId → { history, sessionFacts }

function getSession(id) {
  if (!sessions.has(id)) {
    sessions.set(id, {
      history:      [],  // 对话历史（短期记忆，用于构建 Context）
      sessionFacts: [],  // 本会话提取的临时事实
    });
  }
  return sessions.get(id);
}

// ─────────────────────────────────────────────
//  三层统一检索入口
// ─────────────────────────────────────────────
function retrieveAll(message, session, vectorMem) {
  // Layer 1：会话内事实（全部返回，无需匹配）
  const sessionFacts = session.sessionFacts || [];

  // Layer 2+3：对持久化记忆做向量语义检索
  const vectorHits = vectorMem.search(message, 4, 0.05);

  // 去重：已在持久化里的条目不重复出现在 sessionFacts 里
  const persistedIds  = new Set(vectorHits.map(h => h.id).filter(Boolean));
  const dedupedFacts  = sessionFacts.filter(f => !persistedIds.has(f.id));

  return { sessionFacts: dedupedFacts, vectorHits };
}

// ─────────────────────────────────────────────
//  自动提取：从对话中识别事实存入会话内记忆
//  不持久化，只在本次会话有效
// ─────────────────────────────────────────────
function autoExtractSession(question, session, persistedMem) {
  const nameMatch = question.match(/我叫([^\s，。！？]{1,8})|我是([^\s，。！？]{1,8})/);
  if (!nameMatch) return;

  const name = (nameMatch[1] || nameMatch[2]).trim();
  const inSession   = session.sessionFacts.some(x => x.category === '用户姓名');
  const inPersisted = persistedMem.some(x => x.category === '用户姓名' && x.content === name);

  if (!inSession && !inPersisted) {
    session.sessionFacts.push({
      category: '用户姓名',
      content:  name,
      at:       new Date().toISOString(),
      source:   'auto-session',  // 标记为自动提取，非用户主动保存
    });
  }
}

module.exports = {
  VectorMemory,
  loadPersisted,
  savePersisted,
  getSession,
  retrieveAll,
  autoExtractSession,
};
