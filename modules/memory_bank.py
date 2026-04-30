"""
╔══════════════════════════════════════════════════════╗
║                 Memory Bank 模块                     ║
║                                                      ║
║  三层记忆中的第三层：外部知识库                        ║
║                                                      ║
║  与其他两层的区别：                                   ║
║                                                      ║
║  Short-term (RAM)   → session history                ║
║    存对话上下文，会话结束即消失                        ║
║                                                      ║
║  Long-term (HDD)    → memories.json                 ║
║    存用户个人事实（"我叫张三"、"我喜欢简洁回答"）      ║
║    条目短小，由 save_to_memory 工具写入               ║
║                                                      ║
║  Memory Bank        → memory_bank.json              ║
║    存知识文档（文章、笔记、代码、会议记录等）           ║
║    内容较长，支持语义检索，可动态增删                  ║
║    类比真实场景的向量数据库（Pinecone / pgvector）     ║
║                                                      ║
║  检索机制：复用 VectorMemory 的 TF-IDF 余弦相似度     ║
╚══════════════════════════════════════════════════════╝
"""

import json
import os
from datetime import datetime

from .memory import VectorMemory  # 复用 TF-IDF 向量引擎，避免重复实现

BANK_FILE = os.path.join(os.path.dirname(__file__), '..', 'memory_bank.json')


class MemoryBank:
    """
    动态知识文档库
    - 每条记录是一篇"文档"（有标题、正文、来源）
    - 持久化到 memory_bank.json
    - 用 VectorMemory 对全部文档建立 TF-IDF 索引，支持语义检索
    """

    def __init__(self):
        self._docs: list[dict] = []       # 原始文档列表
        self._index = VectorMemory()      # TF-IDF 向量索引
        self._load()

    # ── 持久化 ────────────────────────────────────
    def _load(self):
        try:
            if os.path.exists(BANK_FILE):
                with open(BANK_FILE, 'r', encoding='utf-8') as f:
                    self._docs = json.load(f)
                # 重建向量索引
                for doc in self._docs:
                    self._index.add({'id': doc['id'],
                                     'category': doc['title'],
                                     'content':  doc['content']})
        except Exception as e:
            print(f'[MemoryBank] 加载失败: {e}')

    def _save(self):
        try:
            with open(BANK_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._docs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'[MemoryBank] 保存失败: {e}')

    # ── 写入 ──────────────────────────────────────
    def add(self, title: str, content: str, source: str = 'user') -> dict:
        """
        存入一篇知识文档

        参数：
          title   - 文档标题（也作为检索关键词的一部分）
          content - 文档正文
          source  - 来源标注（'user' / 'agent' / 自定义）
        """
        doc = {
            'id':      str(int(datetime.now().timestamp() * 1000)),
            'title':   title,
            'content': content,
            'source':  source,
            'at':      datetime.utcnow().isoformat() + 'Z',
        }
        self._docs.append(doc)
        # 加入向量索引（用 title + content 联合建索引）
        self._index.add({'id': doc['id'], 'category': title, 'content': content})
        self._save()
        return doc

    # ── 检索 ──────────────────────────────────────
    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """
        语义检索：返回与 query 最相关的文档

        实现：TF-IDF 余弦相似度（与 Memory Layer 3 相同机制）
        真实生产环境会换成 OpenAI Embeddings + Pinecone 等向量数据库
        """
        hits = self._index.search(query, top_k=top_k, threshold=0.03)
        # 用 id 回查原始文档（索引只存了摘要字段）
        id_map = {d['id']: d for d in self._docs}
        results = []
        for h in hits:
            doc = id_map.get(h.get('id'), {})
            if doc:
                results.append({**doc, 'score': round(h['score'], 3)})
        return results

    # ── 查询 ──────────────────────────────────────
    def list_all(self) -> list[dict]:
        """返回所有文档的摘要（不含全文，避免 token 过长）"""
        return [
            {'id': d['id'], 'title': d['title'],
             'source': d['source'], 'at': d['at'],
             'preview': d['content'][:80] + ('…' if len(d['content']) > 80 else '')}
            for d in self._docs
        ]

    def size(self) -> int:
        return len(self._docs)
