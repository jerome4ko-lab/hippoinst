"""YouTube 통계 폴러 — done 상태 record 들의 viewCount/likeCount/commentCount 갱신.

기본 30분 주기. 영상 1개당 quota 1 unit 이라 일일 4영상 × 48번 = 192 units 정도로
일일 한도(10,000) 의 2% 밖에 안 씀.

비활성화 조건: refresh_token 미설정 시 스킵.

호출자 (web/app.py 의 startup hook):
    stats_poller.start(interval_s=30 * 60)
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import config
from pipeline import upload_store


_started = False
_lock = threading.Lock()


def _enabled() -> bool:
    return bool(
        config.YOUTUBE_CLIENT_ID
        and config.YOUTUBE_CLIENT_SECRET
        and config.YOUTUBE_REFRESH_TOKEN
    )


def refresh_once(*, max_records: Optional[int] = None) -> dict:
    """1회 실행 헬퍼 — done 항목들의 통계를 갱신. 결과 요약 반환.

    UI 의 '🔄 통계 새로고침' 버튼이 호출하기에도 적합.
    """
    if not _enabled():
        return {"updated": 0, "skipped": 0, "error": "youtube_credentials_missing"}

    from pipeline.youtube_publisher import fetch_video_stats

    done = upload_store.list_by_status("done")
    targets = [r for r in done if r.get("video_id")]
    if max_records:
        targets = targets[:max_records]
    if not targets:
        return {"updated": 0, "skipped": 0}

    video_ids = [r["video_id"] for r in targets]
    try:
        stats_map = fetch_video_stats(video_ids)
    except Exception as e:                                          # noqa: BLE001
        return {"updated": 0, "skipped": len(targets), "error": str(e)}

    updated = 0
    fetched_at = upload_store.now_iso()
    for rec in targets:
        s = stats_map.get(rec["video_id"])
        if not s:
            continue
        upload_store.update(rec["id"], stats=s, stats_fetched_at=fetched_at)
        updated += 1
    return {"updated": updated, "skipped": len(targets) - updated, "fetched_at": fetched_at}


def _loop(interval_s: int) -> None:
    while True:
        try:
            if _enabled():
                summary = refresh_once()
                if summary.get("error"):
                    print(f"[stats] poll error: {summary['error']}")
                elif summary.get("updated"):
                    print(f"[stats] {summary['updated']} 건 통계 갱신")
        except Exception as e:                                       # noqa: BLE001
            print(f"[stats] loop error: {e}")
        time.sleep(interval_s)


def start(*, interval_s: int = 30 * 60) -> None:
    """폴러 데몬 1회 기동. 멱등."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    if not _enabled():
        print("[stats] YOUTUBE_REFRESH_TOKEN 없음 - 통계 폴러 비활성화")
        return
    t = threading.Thread(
        target=_loop,
        args=(interval_s,),
        name="stats-poller",
        daemon=True,
    )
    t.start()
