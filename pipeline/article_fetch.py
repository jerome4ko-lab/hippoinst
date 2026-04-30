"""URL → 기사 본문 텍스트 추출.

기사 입력 textarea 에 사용자가 URL 만 붙여넣었을 때, 백엔드에서 자동으로
fetch + 본문 추출하여 Claude 에 진짜 본문을 넘겨주기 위한 변환 layer.

성공한 추출은 sha1(url) 키로 24h 캐시. 실패는 캐싱하지 않음 (재시도 가능).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import trafilatura

import config


# ── Constants ────────────────────────────────────────────────────────────────

URL_RE = re.compile(r"^https?://\S+$")

_CACHE_DIR = config.TEMP_DIR / "article_cache"
_CACHE_TTL = 24 * 3600          # 24시간
_TIMEOUT_S = 8
_MIN_TEXT  = 300                # 본문이 이보다 짧으면 paywall/추출 실패로 간주
_MAX_CACHE_ENTRIES = 100        # LRU eviction 한도

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
}


# ── Public API ────────────────────────────────────────────────────────────────

class ArticleFetchError(Exception):
    """URL fetch / 본문 추출 실패. 메시지는 그대로 사용자 toast 로 노출됨."""
    def __init__(self, idx: int, domain: str, reason: str):
        self.idx = idx
        self.domain = domain
        self.reason = reason
        super().__init__(
            f"기사 {idx + 1} ({domain}) 가져오기 실패: {reason}. "
            f"본문을 직접 붙여주세요."
        )


def is_url(s: str) -> bool:
    """trim 후 단일 URL 패턴인지. 공백 포함되면 False (URL+메모 혼합 케이스)."""
    return bool(URL_RE.match((s or "").strip()))


def fetch_article_text(url: str, *, idx: int = 0) -> str:
    """URL → 본문 텍스트. 캐시 hit 시 즉시 반환.

    실패 시 `ArticleFetchError` raise — 메시지에 사용자 안내 포함.
    """
    url = url.strip()
    domain = _domain(url)

    # 1) 캐시 lookup
    cached = _cache_get(url)
    if cached is not None:
        return cached

    # 2) Fetch
    try:
        resp = requests.get(
            url,
            headers=_HEADERS,
            timeout=_TIMEOUT_S,
            allow_redirects=True,
        )
    except requests.Timeout:
        raise ArticleFetchError(idx, domain, "응답 시간 초과 (8초)")
    except requests.ConnectionError as e:
        raise ArticleFetchError(idx, domain, "연결 실패 (도메인 확인 필요)")
    except requests.RequestException as e:
        raise ArticleFetchError(idx, domain, f"요청 오류: {str(e)[:50]}")

    if resp.status_code >= 400:
        raise ArticleFetchError(idx, domain, f"HTTP {resp.status_code}")

    # 3) 본문 추출
    try:
        text = trafilatura.extract(
            resp.text,
            favor_recall=True,           # 한국어 긴 본문 잘 잡음
            include_comments=False,
            include_tables=False,
            include_images=False,
            deduplicate=True,
        )
    except Exception as e:
        raise ArticleFetchError(idx, domain, f"본문 파싱 오류: {str(e)[:50]}")

    if not text or not text.strip():
        raise ArticleFetchError(idx, domain, "본문을 찾을 수 없음")

    text = text.strip()
    if len(text) < _MIN_TEXT:
        raise ArticleFetchError(
            idx, domain,
            f"본문이 너무 짧음 ({len(text)}자, paywall 의심)"
        )

    # 4) 캐시 store (성공만)
    try:
        _cache_put(url, text)
    except Exception as exc:
        print(f"[article_fetch] cache put 실패 (무시): {exc}", flush=True)
    return text


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _domain(url: str) -> str:
    try:
        return urlparse(url).hostname or "?"
    except Exception:
        return "?"


def _cache_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _cache_path(url: str) -> Path:
    return _CACHE_DIR / f"{_cache_key(url)}.json"


def _cache_get(url: str) -> str | None:
    p = _cache_path(url)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - float(data.get("fetched_at", 0)) > _CACHE_TTL:
            return None
        text = data.get("text") or ""
        if not text:
            return None
        # touch for LRU
        try: p.touch(exist_ok=True)
        except Exception: pass
        return text
    except Exception:
        return None


def _cache_put(url: str, text: str) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _cache_path(url)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({
            "url": url,
            "text": text,
            "fetched_at": time.time(),
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(p)
    _cache_evict()


def _cache_evict(max_entries: int = _MAX_CACHE_ENTRIES) -> None:
    if not _CACHE_DIR.exists():
        return
    entries: list[tuple[float, Path]] = []
    for f in _CACHE_DIR.glob("*.json"):
        try:
            entries.append((f.stat().st_mtime, f))
        except OSError:
            continue
    if len(entries) <= max_entries:
        return
    entries.sort(key=lambda t: t[0])
    for _, f in entries[:len(entries) - max_entries]:
        try: f.unlink(missing_ok=True)
        except Exception: pass
