from __future__ import annotations

import html as html_lib
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from agent.registry import Ctx, ToolSpec
from agent.security import netguard

TimeRange = Literal["", "d", "w", "m", "y"]
TR_HELP = "Filtro de fecha: d=último día, w=última semana, m=último mes, y=último año; vacío=sin filtro"


class _TimeRangeModel(BaseModel):
    time_range: TimeRange = Field("", description=TR_HELP)

    @field_validator("time_range", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return "" if v is None else v


class WebSearchArgs(_TimeRangeModel):
    query: str = Field(..., min_length=1, max_length=300, description="Consulta de búsqueda")
    max_results: int = Field(6, ge=1, le=10, description="Número de resultados")


class WebNewsArgs(_TimeRangeModel):
    query: str = Field(..., min_length=1, max_length=300, description="Tema de las noticias")
    max_results: int = Field(6, ge=1, le=10, description="Número de resultados")
    time_range: TimeRange = Field("w", description=TR_HELP)


class FetchPageArgs(BaseModel):
    url: str = Field(..., min_length=8, max_length=2000, description="URL http/https a descargar")
    max_chars: int = Field(8000, ge=500, le=20000, description="Máximo de caracteres a devolver")


class WebResearchArgs(_TimeRangeModel):
    query: str = Field(..., min_length=1, max_length=300, description="Pregunta o tema a investigar")


# --- red y buscador (puntos de sustitución en tests) ---

def _get(url: str) -> netguard.FetchResult:
    return netguard.safe_get(url)


def _ddgs():
    from ddgs import DDGS

    return DDGS(timeout=15)


def _search_text(query: str, max_results: int, time_range: str) -> list[dict]:
    raw = _ddgs().text(
        query, region="wt-wt", safesearch="moderate", timelimit=time_range or None, max_results=max_results
    ) or []
    return [
        {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
        for r in raw
        if r.get("href")
    ]


def _search_news(query: str, max_results: int, time_range: str) -> list[dict]:
    raw = _ddgs().news(
        query, region="wt-wt", safesearch="moderate", timelimit=time_range or None, max_results=max_results
    ) or []
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("body", ""),
            "date": r.get("date", ""),
            "source": r.get("source", ""),
        }
        for r in raw
        if r.get("url")
    ]


# --- utilidades ---

def _wrap(body: str, source: str = "") -> str:
    safe = body.replace("</web_content", "</web_content_")
    src = source.replace('"', "%22")
    return f'<web_content untrusted source="{src}">\n{safe}\n</web_content>'


def _domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return html_lib.unescape(raw)


def _clean(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def extract_text(raw_html: str, url: str) -> tuple[str, str, str]:
    """Devuelve (título, fecha, texto) de una página HTML."""
    title = date = text = ""
    try:
        import trafilatura

        text = trafilatura.extract(raw_html, url=url, include_comments=False, include_tables=True) or ""
        meta = trafilatura.extract_metadata(raw_html, default_url=url)
        if meta is not None:
            title = getattr(meta, "title", "") or ""
            date = str(getattr(meta, "date", "") or "")
    except Exception:
        pass
    if not text:
        text = _strip_html(raw_html)
    if not title:
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw_html)
        if m:
            title = html_lib.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
    return title.strip(), date.strip(), _clean(text)


def _fetch_page(url: str, max_chars: int) -> dict:
    res = _get(url)
    looks_html = "html" in res.content_type or res.content_type == "" or res.text.lstrip().startswith("<")
    if looks_html:
        title, date, text = extract_text(res.text, res.url)
    else:
        title, date, text = "", "", _clean(res.text)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... recortado]"
    return {"url": res.url, "title": title or _domain(res.url), "date": date, "text": text}


def _load(result: dict, max_chars: int) -> dict | None:
    try:
        page = _fetch_page(result["url"], max_chars)
    except Exception:
        return None
    if not page["text"].strip():
        return None
    if not page["title"] or page["title"] == _domain(page["url"]):
        page["title"] = result.get("title") or page["title"]
    return page


def _fmt_page(n: int, page: dict) -> str:
    date = f" (fecha: {page['date']})" if page.get("date") else ""
    head = f"[{n}] {page['title']} — {page['url']}{date}"
    return head + "\n" + _wrap(page["text"], page["url"])


# --- herramientas ---

def _tool_search(ctx: Ctx, a: WebSearchArgs) -> str:
    results = _search_text(a.query, a.max_results, a.time_range)
    if not results:
        return "Sin resultados."
    lines = [f"[{i}] {r['title']}\n{r['url']}\n{r['snippet']}" for i, r in enumerate(results, 1)]
    return _wrap("\n\n".join(lines), "web_search")


def _tool_news(ctx: Ctx, a: WebNewsArgs) -> str:
    results = _search_news(a.query, a.max_results, a.time_range)
    if not results:
        return "Sin noticias."
    lines = []
    for i, r in enumerate(results, 1):
        meta = " · ".join(x for x in (r.get("source", ""), r.get("date", "")) if x)
        lines.append(f"[{i}] {r['title']}" + (f" ({meta})" if meta else "") + f"\n{r['url']}\n{r['snippet']}")
    return _wrap("\n\n".join(lines), "web_news")


def _tool_fetch(ctx: Ctx, a: FetchPageArgs) -> str:
    page = _fetch_page(a.url, a.max_chars)
    return _fmt_page(1, page)


def _tool_research(ctx: Ctx, a: WebResearchArgs) -> str:
    n_pages, page_chars = 3, ctx.settings.page_chars
    results = _search_text(a.query, 10, a.time_range)
    if not results:
        return "Sin resultados."
    seen: set[str] = set()
    candidates: list[dict] = []
    for r in results:
        d = _domain(r["url"])
        if d and d not in seen:
            seen.add(d)
            candidates.append(r)
    pages: list[dict] = []
    used: set[str] = set()
    idx = 0
    while len(pages) < n_pages and idx < len(candidates):
        batch = candidates[idx: idx + (n_pages - len(pages))]
        idx += len(batch)
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            loaded = list(pool.map(lambda r: _load(r, page_chars), batch))
        for r, page in zip(batch, loaded):
            if page:
                pages.append(page)
                used.add(r["url"])
    out: list[str] = [f"Investigación sobre: {a.query}"]
    if pages:
        out.append("Páginas leídas:")
        out.extend(_fmt_page(i, p) for i, p in enumerate(pages, 1))
    else:
        out.append("No se pudo descargar ninguna página; solo hay snippets.")
    rest = [r for r in results if r["url"] not in used]
    if rest:
        n = len(pages)
        lines = [f"[{n + i}] {r['title']} — {r['url']}\n{r['snippet']}" for i, r in enumerate(rest, 1)]
        out.append("Otros resultados (solo snippets, sin leer):")
        out.append(_wrap("\n\n".join(lines), "snippets"))
    return "\n\n".join(out)


SPECS = [
    ToolSpec(
        "web_search",
        "Busca en internet y devuelve títulos, URLs y snippets. Para datos importantes usa mejor web_research.",
        WebSearchArgs, _tool_search, "web", "research", taints=True,
    ),
    ToolSpec(
        "web_news",
        "Busca noticias recientes sobre un tema (por defecto, de la última semana).",
        WebNewsArgs, _tool_news, "web", "research", taints=True,
    ),
    ToolSpec(
        "fetch_page",
        "Descarga una URL concreta y devuelve su texto principal.",
        FetchPageArgs, _tool_fetch, "web", "research", taints=True,
    ),
    ToolSpec(
        "web_research",
        "Investiga un tema: busca, lee las 3 mejores páginas de dominios distintos y devuelve el texto "
        "numerado [1], [2]… con fecha si existe, más snippets del resto. Úsala para cualquier dato actual o dudoso.",
        WebResearchArgs, _tool_research, "web", "research", taints=True,
    ),
]
