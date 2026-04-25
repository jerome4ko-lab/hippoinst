"""Brave Search News API 래퍼.

A 모델: Brave 응답을 그대로 우리 카드 스키마로 매핑. 시간 역순 보장.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

import requests

import config

_NEWS_URL = "https://api.search.brave.com/res/v1/news/search"

# 주제 → 검색 쿼리. 자주 바꿔도 되는 부분.
_TOPIC_QUERY = {
    "robot": "humanoid robot",
    "ai":    "AI breakthrough",
}


def search_news(topic: str, *, limit: int = 15, freshness: str = "pm") -> list[dict]:
    """topic 키워드로 Brave News 검색 → 시간 역순 카드 리스트.

    freshness: pd(past day) / pw(past week) / pm(past month) / py(past year)
    """
    if not config.BRAVE_API_KEY:
        raise RuntimeError("BRAVE_API_KEY 환경변수가 비어있어요")

    q = _TOPIC_QUERY.get(topic, topic)
    res = requests.get(
        _NEWS_URL,
        params={
            "q":           q,
            "count":       max(1, min(int(limit), 20)),
            "freshness":   freshness,
            "country":     "us",
            "search_lang": "en",
            "spellcheck":  "1",
        },
        headers={
            "X-Subscription-Token": config.BRAVE_API_KEY,
            "Accept":               "application/json",
            "Accept-Encoding":      "gzip",
        },
        timeout=15,
    )
    if res.status_code != 200:
        raise RuntimeError(f"Brave Search 실패 ({res.status_code}): {res.text[:300]}")

    data = res.json() or {}
    raw  = (data.get("results") or [])

    items: list[dict] = []
    for r in raw:
        items.append(_to_card(r, fallback_keyword=q))

    # 시간 역순 (page_age 우선, 없으면 age 텍스트로 보조 정렬)
    items.sort(key=lambda x: x.get("_sort") or "", reverse=True)
    for it in items:
        it.pop("_sort", None)
    return items


def _to_card(r: dict, *, fallback_keyword: str) -> dict:
    page_age   = (r.get("page_age") or "").strip()    # ISO8601 e.g. 2026-04-19T12:34:56
    age_text   = (r.get("age") or "").strip()          # "2 hours ago"
    title      = (r.get("title") or "").strip()
    url        = (r.get("url") or "").strip()
    desc       = _clean_html(r.get("description") or "")
    meta       = r.get("meta_url") or {}
    source     = (meta.get("hostname") or "").strip()
    thumbnail  = ((r.get("thumbnail") or {}).get("src") or "").strip()

    date_str   = _date_only(page_age) or _approx_date_from_age(age_text)
    sort_key   = page_age or _age_to_pseudo_iso(age_text)

    return {
        "date":      date_str,
        "title":     title,
        "keyword":   _short_keyword(title) or fallback_keyword,
        "summary":   desc,
        "links":     [url] if url else [],     # A 모델: 기사 본문 URL 1개
        "source":    source,
        "thumbnail": thumbnail,
        "age":       age_text,
        "_sort":     sort_key,
    }


# ── helpers ──────────────────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")


def _clean_html(s: str) -> str:
    return _TAG_RE.sub("", s).strip()


def _date_only(iso: str) -> Optional[str]:
    if not iso:
        return None
    try:
        return iso[:10]   # 'YYYY-MM-DD'
    except Exception:
        return None


def _short_keyword(title: str, max_words: int = 6) -> str:
    """제목에서 노이즈 제거 후 앞 6단어만 → YouTube 검색용 키워드."""
    t = re.sub(r"[\"'|\-—–:•\[\]\(\)]", " ", title)
    t = re.sub(r"\s+", " ", t).strip()
    return " ".join(t.split()[:max_words])


def _approx_date_from_age(age: str) -> Optional[str]:
    """'2 hours ago' 류 → 오늘 날짜로 근사. (page_age 없을 때만)"""
    if not age:
        return None
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _age_to_pseudo_iso(age: str) -> str:
    """page_age가 비었을 때 정렬용 가짜 ISO 문자열. age 텍스트가 없으면 빈 문자열."""
    if not age:
        return ""
    # 단순화: 단위별 가중치로 점수만 만들고 ISO 흉내. 정확한 시각 불요, 정렬 안정성만.
    units = [("minute", 60), ("hour", 3600), ("day", 86400),
             ("week", 604800), ("month", 2592000), ("year", 31536000)]
    m = re.match(r"(\d+)\s+(\w+)", age)
    if not m:
        return ""
    n, unit = int(m.group(1)), m.group(2).rstrip("s")
    secs = next((s for u, s in units if u == unit), 86400) * n
    ts   = datetime.now(timezone.utc).timestamp() - secs
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
