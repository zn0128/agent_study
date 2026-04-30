"""
╔══════════════════════════════════════════════════════╗
║                   Memory 模块                        ║
║                                                      ║
║  负责 Agent 的三层记忆体系：                           ║
║                                                      ║
║  Layer 1 · 会话内记忆 (Session Memory)               ║
║    - 存在于当前会话的 RAM 中                          ║
║    - 会话结束即失效                                   ║
║    - 来源：本轮对话自动提取的临时事实                  ║
║                                                      ║
║  Layer 2 · 持久化记忆 (Persistent Memory)            ║
║    - 写入磁盘 memories.json                          ║
║    - 服务器重启后依然存在                             ║
║    - 来源：用户明确要求 save_to_memory 时写入         ║
║                                                      ║
║  Layer 3 · 向量语义检索 (Vector Memory)              ║
║    - 基于 TF-IDF 余弦相似度的语义匹配                 ║
║    - 对持久化记忆建立向量索引                         ║
║    - 每次对话按相关度召回最匹配的记忆                  ║
╚══════════════════════════════════════════════════════╝
"""

import json
import math
import os
import re
from collections import defaultdict

# ─────────────────────────────────────────────
#  Layer 3：TF-IDF 向量引擎
#
#  原理：
#    TF  (词频)      = 某词在文档中出现的频率
#    IDF (逆文档频率) = log((总文档数+1) / (含该词的文档数+1)) + 1
#    TF-IDF 向量      = 每个词的 TF × IDF 权重
#    余弦相似度        = 两个向量的点积 / (模长之积)
#
#  为什么用 TF-IDF 而不是关键词匹配？
#    关键词匹配："我叫张三" vs "你叫什么" → 无公共词 → 匹配失败
#    TF-IDF 字符分词："叫" 同时出现在两句 → 有相似度
# ─────────────────────────────────────────────
class VectorMemory:
    def __init__(self):
        self.docs = []   # 所有文档（含向量）
        self.idf  = {}   # 每个 token 的 IDF 权重

    # 分词器：英文按空格切词；中文拆成单字 + 相邻双字 bigram
    # 例："用户叫张三" → ['用','户','叫','张','三','用户','户叫','叫张','张三']
    def _tokenize(self, text: str) -> list[str]:
        tokens = []
        lower  = text.lower()
        parts  = re.split(r'[^\w\u4e00-\u9fff]+', lower)
        parts  = [p for p in parts if p]

        for part in parts:
            if re.search(r'[\u4e00-\u9fff]', part):
                # 中文单字
                for ch in part:
                    if re.match(r'[\u4e00-\u9fff]', ch):
                        tokens.append(ch)
                # 中文 bigram（增强上下文语义）
                for i in range(len(part) - 1):
                    if (re.match(r'[\u4e00-\u9fff]', part[i]) and
                            re.match(r'[\u4e00-\u9fff]', part[i + 1])):
                        tokens.append(part[i] + part[i + 1])
            elif part:
                tokens.append(part)  # 英文整词

        return tokens

    # 重建全量 IDF 并刷新所有文档的 TF-IDF 向量
    # 每次新增文档后调用，因为新文档会改变 IDF 值
    def _rebuild(self):
        n  = len(self.docs)
        df = defaultdict(int)

        for doc in self.docs:
            for token in set(doc['tokens']):
                df[token] += 1

        # IDF = log((N+1)/(df+1)) + 1，加 1 防止除零，稀有词权重更高
        self.idf = {
            token: math.log((n + 1) / (count + 1)) + 1
            for token, count in df.items()
        }

        # 更新每篇文档的 TF-IDF 向量
        for doc in self.docs:
            vec = {}
            for token, freq in doc['tf'].items():
                vec[token] = (freq / len(doc['tokens'])) * self.idf.get(token, 1)
            doc['vec'] = vec

    # 添加一条记忆到向量索引
    def add(self, entry: dict):
        text   = f"{entry.get('category', '')} {entry.get('content', '')}"
        tokens = self._tokenize(text)
        tf     = defaultdict(int)
        for t in tokens:
            tf[t] += 1

        doc = {**entry, 'tokens': tokens, 'tf': dict(tf), 'vec': None}
        self.docs.append(doc)
        self._rebuild()

    # 余弦相似度计算
    @staticmethod
    def _cosine(vec_a: dict, vec_b: dict) -> float:
        all_keys = set(vec_a) | set(vec_b)
        dot = norm_a = norm_b = 0.0
        for k in all_keys:
            a = vec_a.get(k, 0)
            b = vec_b.get(k, 0)
            dot   += a * b
            norm_a += a * a
            norm_b += b * b
        return dot / math.sqrt(norm_a * norm_b) if norm_a and norm_b else 0.0

    # 语义搜索：返回与 query 最相似的 top_k 条记忆
    def search(self, query: str, top_k: int = 4, threshold: float = 0.05) -> list[dict]:
        if not self.docs:
            return []

        # 构建 query 的 TF-IDF 向量
        q_tokens = self._tokenize(query)
        q_tf     = defaultdict(int)
        for t in q_tokens:
            q_tf[t] += 1

        q_vec = {
            t: (freq / len(q_tokens)) * self.idf.get(t, 1)
            for t, freq in q_tf.items()
        } if q_tokens else {}

        # 对所有文档计算相似度，过滤、排序、截取
        scored = [
            {**doc, 'score': self._cosine(q_vec, doc.get('vec') or {})}
            for doc in self.docs
        ]
        return sorted(
            [d for d in scored if d['score'] > threshold],
            key=lambda d: d['score'],
            reverse=True,
        )[:top_k]

    def size(self)  -> int:  return len(self.docs)
    def clear(self):         self.docs = []; self.idf = {}

    # 从持久化数组重建整个索引（服务器启动时调用）
    def rebuild(self, entries: list[dict]):
        self.clear()
        for e in entries:
            self.add(e)


# ─────────────────────────────────────────────
#  Layer 2：持久化存储（磁盘文件）
# ─────────────────────────────────────────────
MEMORY_FILE = os.path.join(os.path.dirname(__file__), '..', 'memories.json')

def load_persisted() -> list[dict]:
    """启动时从磁盘加载"""
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f'[Memory] 加载持久化记忆失败: {e}')
    return []

def save_persisted(persisted_mem: list[dict]):
    """每次新增记忆后写入磁盘"""
    try:
        with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(persisted_mem, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'[Memory] 保存持久化记忆失败: {e}')


# ─────────────────────────────────────────────
#  Layer 1：会话存储（RAM）
# ─────────────────────────────────────────────
_sessions: dict[str, dict] = {}  # session_id → { history, session_facts }

def get_session(session_id: str) -> dict:
    if session_id not in _sessions:
        _sessions[session_id] = {
            'history':       [],  # 对话历史（短期记忆，用于构建 Context）
            'session_facts': [],  # 本会话提取的临时事实
        }
    return _sessions[session_id]


# ─────────────────────────────────────────────
#  三层统一检索入口
# ─────────────────────────────────────────────
def retrieve_all(message: str, session: dict, vector_mem: VectorMemory) -> dict:
    # Layer 1：会话内事实（全部返回，无需匹配）
    session_facts = session.get('session_facts', [])

    # Layer 2+3：对持久化记忆做向量语义检索
    vector_hits = vector_mem.search(message, 4, 0.05)

    # 去重：已在持久化里的条目不重复出现在 session_facts 里
    persisted_ids  = {h['id'] for h in vector_hits if h.get('id')}
    deduped_facts  = [f for f in session_facts if f.get('id') not in persisted_ids]

    return {'session_facts': deduped_facts, 'vector_hits': vector_hits}


# ─────────────────────────────────────────────
#  自动提取：从对话中识别事实存入会话内记忆
#  不持久化，只在本次会话有效
# ─────────────────────────────────────────────
def auto_extract_session(question: str, session: dict, persisted_mem: list[dict]):
    match = re.search(r'我叫([^\s，。！？]{1,8})|我是([^\s，。！？]{1,8})', question)
    if not match:
        return

    name = (match.group(1) or match.group(2)).strip()
    in_session   = any(x.get('category') == '用户姓名' for x in session['session_facts'])
    in_persisted = any(
        x.get('category') == '用户姓名' and x.get('content') == name
        for x in persisted_mem
    )

    if not in_session and not in_persisted:
        from datetime import datetime, timezone
        session['session_facts'].append({
            'category': '用户姓名',
            'content':  name,
            'at':       datetime.now(timezone.utc).isoformat(),
            'source':   'auto-session',  # 标记为自动提取，非用户主动保存
        })
