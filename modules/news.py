"""
╔══════════════════════════════════════════════════════╗
║                   News 模块                          ║
║                                                      ║
║  通过 RSS Feed 获取实时新闻                           ║
║                                                      ║
║  为什么用 RSS 而不是新闻 API？                        ║
║    - 无需额外 API Key                                ║
║    - 主流媒体均提供免费 RSS                           ║
║    - 数据结构标准（XML），解析简单                    ║
║                                                      ║
║  可用源（在当前网络环境中经过验证）：                  ║
║    general    Hacker News 前页（综合科技讨论）        ║
║    technology MIT Technology Review / HN Best        ║
║    science    Nature / NASA                          ║
╚══════════════════════════════════════════════════════╝
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

# ─────────────────────────────────────────────
#  RSS Feed 地址表（仅保留当前网络可达的源）
#  每个类别多个备用，按顺序尝试
# ─────────────────────────────────────────────
RSS_FEEDS: dict[str, list[str]] = {
    'general': [
        'https://hnrss.org/frontpage',
        'https://hnrss.org/best',
    ],
    'technology': [
        'https://www.technologyreview.com/feed/',
        'https://hnrss.org/best',
        'https://hnrss.org/frontpage',
    ],
    'business': [
        'https://hnrss.org/best',
        'https://hnrss.org/frontpage',
    ],
    'world': [
        'https://hnrss.org/frontpage',
        'https://hnrss.org/best',
    ],
    'science': [
        'https://www.nature.com/nature.rss',
        'https://www.nasa.gov/news-release/feed/',
        'https://hnrss.org/frontpage',
    ],
}

_HTML_TAG = re.compile(r'<[^>]+>')
_RSS1_NS  = 'http://purl.org/rss/1.0/'   # RSS 1.0 (RDF) 命名空间


def _clean(text: str, max_len: int = 200) -> str:
    return _HTML_TAG.sub('', text).strip()[:max_len]


def _parse_items(root: ET.Element, count: int) -> list[dict]:
    """兼容 RSS 2.0 和 RSS 1.0 (RDF) 格式"""
    # RSS 2.0：<item> 无命名空间
    items = root.findall('.//item')
    # RSS 1.0 (RDF)：<item> 带命名空间
    if not items:
        items = root.findall(f'.//{{{_RSS1_NS}}}item')

    news = []
    for item in items[:count]:
        def ft(tag):
            v = item.findtext(tag) or item.findtext(f'{{{_RSS1_NS}}}{tag}') or ''
            return _clean(v)

        title = ft('title')
        if title:
            news.append({
                'title':     title,
                'summary':   ft('description') or ft('summary'),
                'published': ft('pubDate') or ft('date'),
                'link':      (item.findtext('link') or item.findtext(f'{{{_RSS1_NS}}}link') or '').strip(),
            })
    return news


# ─────────────────────────────────────────────
#  获取新闻（同步）
#  依次尝试该类别的备用源，任一成功即返回
#
#  参数：
#    topic  - 新闻类别（见 RSS_FEEDS）
#    count  - 返回条数
# ─────────────────────────────────────────────
def fetch_news(topic: str = 'general', count: int = 5) -> dict:
    topic_key = topic.lower()
    if topic_key not in RSS_FEEDS:
        topic_key = 'general'

    urls = RSS_FEEDS[topic_key]
    last_error = ''

    for url in urls:
        try:
            resp = httpx.get(
                url,
                timeout=10,
                verify=False,        # 公司网络 SSL 拦截
                follow_redirects=True,
                headers={'User-Agent': 'Mozilla/5.0'},
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)

            news = _parse_items(root, count)
            if not news:
                continue

            # 找到源名称（RSS 2.0 或 RDF 格式）
            source = (root.findtext('.//channel/title')
                      or root.findtext(f'.//{{{_RSS1_NS}}}channel/{{{_RSS1_NS}}}title')
                      or url)

            return {
                'topic':      topic,
                'source':     _clean(source, 80),
                'count':      len(news),
                'fetched_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'news':       news,
            }

        except Exception as e:
            last_error = str(e)
            continue  # 尝试下一个备用源

    return {'error': f'所有新闻源均不可达：{last_error}'}
