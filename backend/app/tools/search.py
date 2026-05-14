"""Web search tool — supports DDGS (free, no key) and Bocha (Chinese-optimized)."""

from __future__ import annotations

from app.config import settings
from app.tools.base import tool


@tool(
    name="search_web",
    description="搜索互联网获取最新信息，支持中英文关键词",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "num_results": {"type": "integer", "description": "返回结果数量，默认5"},
        },
        "required": ["query"],
    },
)
def search_web(query: str, num_results: int = 5) -> str:
    backend = settings.search_backend

    if backend == "bocha" and settings.bocha_api_key:
        return _search_bocha(query, num_results)
    else:
        return _search_ddgs(query, num_results)


def _search_ddgs(query: str, num_results: int) -> str:
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=num_results, backend="html"))
        if not results:
            return f"未找到关于 '{query}' 的搜索结果。请尝试更换关键词。"
        return _format_results(results)
    except ImportError:
        return "搜索功能不可用：请安装 ddgs (pip install ddgs)"
    except Exception as e:
        return f"搜索失败 (ddgs): {e}"


def _search_bocha(query: str, num_results: int) -> str:
    try:
        import httpx

        resp = httpx.post(
            "https://api.bochaai.com/v1/web-search",
            headers={"Authorization": f"Bearer {settings.bocha_api_key}"},
            json={"query": query, "count": num_results, "freshness": "noLimit"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 200:
            return f"博查搜索失败: {data.get('msg', '未知错误')}"

        webpages = data.get("data", {}).get("webPages", {}).get("value", [])
        if not webpages:
            return f"未找到关于 '{query}' 的搜索结果。"

        results = []
        for p in webpages:
            results.append({
                "title": p.get("name", ""),
                "href": p.get("url", ""),
                "body": p.get("summary", "") or p.get("snippet", ""),
            })
        return _format_results(results)
    except ImportError:
        return "搜索功能不可用：请安装 httpx (pip install httpx)"
    except Exception as e:
        return f"博查搜索失败: {e}"


def _format_results(results: list[dict]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "").strip()
        href = r.get("href", "").strip()
        body = r.get("body", "").strip()
        lines.append(f"[{i}] {title}\n    {body}\n    {href}")
    return "\n\n".join(lines)
