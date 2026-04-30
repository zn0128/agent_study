"""
╔══════════════════════════════════════════════════════╗
║                  Workflow 模块                       ║
║                                                      ║
║  Workflow = 预定义的多步骤执行引擎                    ║
║                                                      ║
║  与 Planning 的区别：                                ║
║    Planning  → 运行时由 LLM 动态决定（灵活）          ║
║    Workflow  → 人预定义的固定步骤（确定性）            ║
║                                                      ║
║  已注册模板：                                        ║
║    complex_analysis - 复杂任务预处理                 ║
║      Step 1: extract_topics  提取消息中的关键话题    ║
║      Step 2: search_knowledge 检索相关知识           ║
║      Step 3: check_news      判断是否需要实时新闻    ║
║                                                      ║
║  工作流结果作为额外 Context 注入 LLM，               ║
║  提升复杂任务的回答质量                               ║
╚══════════════════════════════════════════════════════╝
"""

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .rag  import search_knowledge_base
from .news import fetch_news


@dataclass
class WorkflowStep:
    name:        str
    description: str
    fn:          Callable
    result:      Any    = None
    status:      str    = 'pending'  # pending / done / failed


class WorkflowEngine:
    """
    工作流执行引擎

    流程：
      1. 用 create() 从模板生成步骤列表
      2. 用 run() 依次执行每个步骤
      3. 每个步骤可以访问前面步骤的结果（results dict）
    """

    def __init__(self):
        self._templates: dict[str, list[dict]] = {}
        self._register_defaults()

    def _register_defaults(self):
        """注册内置工作流模板"""
        self._templates['complex_analysis'] = [
            {
                'name':        'extract_topics',
                'description': '从用户消息中提取关键话题',
                'fn':          _step_extract_topics,
            },
            {
                'name':        'search_knowledge',
                'description': '检索话题相关知识',
                'fn':          _step_search_knowledge,
            },
            {
                'name':        'check_news',
                'description': '判断是否需要获取实时新闻',
                'fn':          _step_check_news,
            },
        ]

    def create(self, template: str) -> list[WorkflowStep]:
        steps_def = self._templates.get(template, [])
        return [
            WorkflowStep(name=d['name'], description=d['description'], fn=d['fn'])
            for d in steps_def
        ]

    async def run(self, template: str, context: dict) -> dict:
        """
        执行工作流，返回所有步骤的结果。

        context 必须包含 'message' 字段。
        """
        steps   = self.create(template)
        results = {}

        for step in steps:
            step.status = 'running'
            try:
                step.result = step.fn(context, results)
                step.status = 'done'
                results[step.name] = step.result
            except Exception as e:
                step.status = 'failed'
                results[step.name] = {'error': str(e)}

        return {
            'template': template,
            'steps':    [{'name': s.name, 'status': s.status,
                          'description': s.description} for s in steps],
            'results':  results,
        }

    def format_for_context(self, wf_result: dict) -> str:
        """将工作流结果格式化为可注入 LLM Context 的文本"""
        lines = []
        results = wf_result.get('results', {})

        # 知识检索结果
        kb = results.get('search_knowledge', {})
        if kb.get('hits'):
            lines.append('[工作流预检索知识]')
            for h in kb['hits'][:3]:
                lines.append(f"  · {h.get('topic','')}: {h.get('content','')[:80]}")

        # 新闻结果
        news = results.get('check_news', {})
        if news.get('news'):
            lines.append('[工作流实时新闻摘要]')
            for n in news['news'][:2]:
                lines.append(f"  · {n['title']}")

        return '\n'.join(lines)


# ─────────────────────────────────────────────
#  工作流步骤函数
# ─────────────────────────────────────────────

def _step_extract_topics(context: dict, results: dict) -> dict:
    """从用户消息中提取关键话题（2 字以上中文词 + 4 字以上英文词）"""
    msg    = context.get('message', '')
    tokens = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}', msg)
    unique = list(dict.fromkeys(tokens))[:5]   # 去重保序，最多 5 个
    return {'topics': unique, 'message_len': len(msg)}


def _step_search_knowledge(context: dict, results: dict) -> dict:
    """对提取的话题逐个检索知识库，合并结果去重"""
    topics = results.get('extract_topics', {}).get('topics', [])
    hits   = []
    seen   = set()

    for topic in topics[:3]:
        r = search_knowledge_base(topic)
        if r.get('found'):
            for item in r.get('results', []):
                key = item.get('topic', '')
                if key not in seen:
                    seen.add(key)
                    hits.append(item)

    return {'hits': hits[:5], 'topic_count': len(topics)}


def _step_check_news(context: dict, results: dict) -> dict:
    """判断消息是否含新闻意图关键词，若有则抓取"""
    msg          = context.get('message', '')
    news_signals = ['新闻', '最新', '今天', '动态', '发展', '现在', '近期']

    if any(kw in msg for kw in news_signals):
        return fetch_news('general', 3)

    return {'skipped': True, 'reason': '未检测到新闻意图'}
