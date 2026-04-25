"""Klipy GIF 검색·다운로드. 결과 미디어를 temp/에 캐시한 뒤 Path 반환."""
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

import config

_KLIPY_BASE = "https://api.klipy.com/api/v1"

# mp4 우선 — ffmpeg/Remotion <Video> 모두 안전, Klipy md/mp4는 약 300KB로 가장 작음.
# (로컬 ffmpeg가 animated WebP 디코드 못 하는 경우가 흔하므로 mp4를 1순위.)
_FORMAT_PRIORITY = ("mp4", "webm", "webp", "gif")

# 480px 스티커 기준에 적합한 사이즈 티어 (md ~496px > hd > sm > xs)
_TIER_PRIORITY = ("md", "hd", "sm", "xs")


def search(keyword: str, per_page: int = 5, customer_id: str | None = None) -> list[dict]:
    """Klipy /gifs/search 호출. 결과 아이템 리스트 반환 (없으면 빈 리스트)."""
    if not config.KLIPY_API_KEY:
        raise RuntimeError("KLIPY_API_KEY not set in environment/.env")

    cid = customer_id or config.KLIPY_CUSTOMER_ID
    qs = urllib.parse.urlencode({
        "q":           keyword,
        "per_page":    str(per_page),
        "customer_id": cid,
    })
    url = f"{_KLIPY_BASE}/{config.KLIPY_API_KEY}/gifs/search?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "hippoinst/0.1"})

    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return (data.get("data") or {}).get("data") or []


def pick_media(item: dict) -> tuple[str, str]:
    """item.file 트리에서 (url, ext) 페어 선택. 우선순위: tier(md>hd>sm>xs) × format(webp>gif>mp4>webm)."""
    tree = item.get("file") or {}
    for tier in _TIER_PRIORITY:
        formats = tree.get(tier) or {}
        for fmt in _FORMAT_PRIORITY:
            entry = formats.get(fmt)
            if isinstance(entry, dict) and entry.get("url"):
                return entry["url"], fmt
    raise ValueError(f"No usable media URL in item id={item.get('id')}")


def fetch(keyword: str, customer_id: str | None = None) -> Path:
    """키워드 → 첫 결과 → 적절한 포맷 다운로드 → temp/ 캐시. Path 반환."""
    config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slugify(keyword)

    for ext in _FORMAT_PRIORITY:
        cached = config.TEMP_DIR / f"gif_{slug}.{ext}"
        if cached.exists() and cached.stat().st_size > 0:
            return cached

    items = search(keyword, customer_id=customer_id)
    if not items:
        raise RuntimeError(f"Klipy: no results for {keyword!r}")

    url, ext = pick_media(items[0])
    out = config.TEMP_DIR / f"gif_{slug}.{ext}"
    _download(url, out)
    return out


def _download(url: str, dest: Path) -> None:
    """User-Agent 헤더 포함 다운로드 (Klipy CDN은 빈 UA에 403 응답)."""
    req = urllib.request.Request(url, headers={"User-Agent": "hippoinst/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
        while chunk := r.read(64 * 1024):
            f.write(chunk)


def _slugify(keyword: str) -> str:
    s = keyword.lower().strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_-]+", "", s)
    return s[:48] or "gif"


if __name__ == "__main__":
    import sys
    kw = " ".join(sys.argv[1:]) or "robot dancing"
    p = fetch(kw)
    print(f"{p}  ({p.stat().st_size:,} bytes)")
