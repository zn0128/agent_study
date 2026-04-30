"""
╔══════════════════════════════════════════════════════╗
║                   Skills 模块                        ║
║                                                      ║
║  技能（Skills）= 工具结果的后处理层                   ║
║                                                      ║
║  调用链：                                            ║
║    Tools 执行 → 返回原始结果                         ║
║    Skills 激活 → 对结果进行增强处理                  ║
║    增强后结果 → 回传给 LLM                           ║
║                                                      ║
║  每个 Skill：                                        ║
║    triggers  - 哪些工具执行后触发                    ║
║    process   - 具体的后处理函数                      ║
║                                                      ║
║  已注册技能：                                        ║
║    knowledge_integration  整合多条知识片段           ║
║    news_digest            提炼新闻摘要               ║
║    calculation_context    为计算结果添加量级说明      ║
╚══════════════════════════════════════════════════════╝
"""

import re

# ─────────────────────────────────────────────
#  技能注册表
# ─────────────────────────────────────────────
_REGISTRY: dict[str, dict] = {}


def _register(name: str, description: str, triggers: list[str], process_fn):
    _REGISTRY[name] = {
        'name':        name,
        'description': description,
        'triggers':    triggers,
        'process':     process_fn,
    }


def activate(tool_name: str, tool_result: dict) -> tuple[dict, str | None]:
    """
    根据工具名称激活对应技能，对结果进行后处理。

    返回：(处理后的结果, 技能名称) 或 (原始结果, None)
    """
    for skill in _REGISTRY.values():
        if tool_name in skill['triggers']:
            try:
                enhanced = skill['process'](tool_result)
                return enhanced, skill['name']
            except Exception:
                return tool_result, None
    return tool_result, None


def list_skills() -> list[dict]:
    return [{'name': s['name'], 'description': s['description'],
             'triggers': s['triggers']} for s in _REGISTRY.values()]


# ─────────────────────────────────────────────
#  Skill 1：knowledge_integration
#  整合多条知识片段，生成结构化摘要
# ─────────────────────────────────────────────
def _knowledge_integration(result: dict) -> dict:
    if not result.get('found') or not result.get('results'):
        return result

    items = result['results']

    # 提取每条知识的核心句（第一句）
    summaries = []
    for item in items:
        content = item.get('content', '')
        first_sentence = re.split(r'[。；\n]', content)[0].strip()
        summaries.append(f"[{item.get('topic', '?')}] {first_sentence}")

    result['_skill'] = 'knowledge_integration'
    result['_skill_summary'] = '\n'.join(summaries)
    result['_skill_topic_count'] = len(items)
    return result


# ─────────────────────────────────────────────
#  Skill 2：news_digest
#  从新闻列表提炼关键标题和简报
# ─────────────────────────────────────────────
def _news_digest(result: dict) -> dict:
    if 'error' in result or not result.get('news'):
        return result

    news = result['news']
    # 提取前 3 条标题作为摘要
    headlines = [f"{i + 1}. {n['title']}" for i, n in enumerate(news[:3])]

    result['_skill'] = 'news_digest'
    result['_skill_digest'] = '\n'.join(headlines)
    result['_skill_source'] = result.get('source', '')
    result['_skill_count']  = len(news)
    return result


# ─────────────────────────────────────────────
#  Skill 3：calculation_context
#  为计算结果添加量级说明，让 LLM 更易描述
# ─────────────────────────────────────────────
def _calculation_context(result: dict) -> dict:
    if 'error' in result:
        return result

    val = result.get('result')
    if not isinstance(val, (int, float)):
        return result

    context = ''
    abs_val = abs(val)
    if abs_val >= 1e12:
        context = f'约 {val / 1e12:.2f} 万亿'
    elif abs_val >= 1e8:
        context = f'约 {val / 1e8:.2f} 亿'
    elif abs_val >= 1e4:
        context = f'约 {val / 1e4:.2f} 万'
    elif abs_val > 0 and abs_val < 0.01:
        context = f'科学计数法：{val:.2e}'

    result['_skill'] = 'calculation_context'
    if context:
        result['_skill_context'] = context
    return result


# 注册所有技能
_register('knowledge_integration', '整合知识库检索结果，提取核心信息',
          ['search_knowledge_base', 'search_memory_bank'], _knowledge_integration)

_register('news_digest', '提炼新闻要点，生成简洁摘要',
          ['get_news'], _news_digest)

_register('calculation_context', '为计算结果添加量级说明',
          ['calculate'], _calculation_context)
