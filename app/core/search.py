"""检索服务：查询编排 → 原文高亮片段 + 命中偏移（供前端跳转定位）。"""

from __future__ import annotations

from app.core.indexer.service import IndexService


class SearchService:
    """包装 IndexService，负责结果展示层：分页、高亮片段、原文定位。"""

    def __init__(self, index: IndexService) -> None:
        self.index = index

    def search(self, q: str, page: int = 1, page_size: int = 20) -> dict:
        if not q or not q.strip():
            return {"total": 0, "page": page, "pageSize": page_size, "results": []}
        offset = max((page - 1) * page_size, 0)
        total = self.index.count(q)
        rows = self.index.search_rows(q, page_size, offset)
        results = []
        for row in rows:
            snippet_text, offset_chars = _make_snippet(row.body_original, q)
            results.append(
                {
                    "path": row.path,
                    "title": row.title,
                    "score": None,  # FTS5 rank 已用于排序，此处占位
                    "snippets": (
                        [{"text": snippet_text, "offset": offset_chars, "length": len(snippet_text)}]
                        if snippet_text
                        else []
                    ),
                    "tags": _split_tags(row.tags),
                }
            )
        return {"total": total, "page": page, "pageSize": page_size, "results": results}


def _split_tags(tags: str) -> list[str]:
    return [t for t in tags.replace(" ", "").split(",") if t] if tags else []


def _make_snippet(original: str, query: str, context: int = 40) -> tuple[str, int]:
    """在原文中定位首个命中词，返回 (高亮片段, 字符偏移)。

    优先整串查找（用户输入的连续串），失败则回退到首个分词词。
    """
    if not original:
        return "", 0
    pos = original.find(query)
    if pos < 0:
        from app.core.indexer.fts5 import tokenize_query

        first = next((w for w in tokenize_query(query).split(" ") if w and w not in ('"', "")), None)
        first = (first or "").strip('"')
        if first:
            pos = original.find(first)
    if pos < 0:
        pos = 0
    start = max(pos - context, 0)
    end = min(pos + len(query if query in original else original) + context * 2, len(original))
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(original) else ""
    text = prefix + original[start:end] + suffix
    return text, start


def _first_token(query: str) -> str:
    from app.core.indexer.fts5 import tokenize_query

    q = tokenize_query(query)
    return next((t for t in q.split() if t), "")
