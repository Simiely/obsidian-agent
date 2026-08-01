"""检索服务：查询编排 → 原文高亮片段 + 命中偏移（供前端跳转定位）。"""

from __future__ import annotations

from typing import Any

from app.core.indexer.service import IndexService


class SearchService:
    """包装 IndexService，负责结果展示层：分页、高亮片段、原文定位。"""

    def __init__(self, index: IndexService) -> None:
        self.index = index

    def search(self, q: str, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        if not q or not q.strip():
            return {"total": 0, "page": page, "pageSize": page_size, "results": []}
        offset = max((page - 1) * page_size, 0)
        total = self.index.count(q)
        rows = self.index.search_rows(q, page_size, offset)
        results = []
        for row in rows:
            snippet_text, offset_chars, hit_words = _make_snippet(row.body_original, q)
            results.append(
                {
                    "path": row.path,
                    "title": row.title,
                    "score": None,  # FTS5 rank 已用于排序，此处占位
                    "snippets": (
                        [
                            {
                                "text": snippet_text,
                                "offset": offset_chars,
                                "length": len(snippet_text),
                                "hitWords": hit_words,  # 片段内实际命中的查询词（前端高亮用）
                            }
                        ]
                        if snippet_text
                        else []
                    ),
                    "tags": _split_tags(row.tags),
                }
            )
        return {"total": total, "page": page, "pageSize": page_size, "results": results}


def _split_tags(tags: str) -> list[str]:
    return [t for t in tags.replace(" ", "").split(",") if t] if tags else []


def _query_words(query: str) -> list[str]:
    """查询词候选：整串优先，再补 jieba 分词词，去重保序。"""
    from app.core.indexer.fts5 import tokenize_query

    words: list[str] = []
    raw = query.strip()
    if raw:
        words.append(raw)
    for w in tokenize_query(query).split(" "):
        w = w.strip().strip('"')
        if w and w not in words:
            words.append(w)
    return words


def _find_ci(haystack: str, needle: str) -> int:
    """大小写不敏感查找，返回在原串中的字节/字符位置（找不到返回 -1）。

    FTS5 unicode61 分词默认对 ASCII 大小写不敏感，检索层必须保持一致：
    否则搜「obsidian」时原文「Obsidian」无法定位、无法高亮（坑：find 区分大小写）。
    """
    return haystack.lower().find(needle.lower())


def _make_snippet(original: str, query: str, context: int = 40) -> tuple[str, int, list[str]]:
    """在原文中定位首个命中词，返回 (高亮片段, 字符偏移, 片段内命中词列表)。

    优先整串查找（用户输入的连续串，英文大小写不敏感），失败则回退到首个分词词；
    hit_words 供前端渲染 <mark> 高亮，只含片段内实际出现的词（英文大小写不敏感匹配）。
    """
    if not original:
        return "", 0, []
    pos = _find_ci(original, query)
    if pos < 0:
        first = next((w for w in _query_words(query)[1:]), None)
        if first:
            pos = _find_ci(original, first)
    if pos < 0:
        pos = 0
    # 命中词长度：整串命中用整串长度，回退分词时用分词长度
    hit_len = len(query) if _find_ci(original, query) >= 0 else len(first or query)
    start = max(pos - context, 0)
    end = min(pos + hit_len + context * 2, len(original))
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(original) else ""
    text = prefix + original[start:end] + suffix
    lowered = text.lower()
    hit_words = [w for w in _query_words(query) if w.lower() in lowered]
    return text, start, hit_words
