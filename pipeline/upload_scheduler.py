"""60초 주기 폴러 스케줄러.

기동
─────
    upload_scheduler.start(callback, interval_s=60)

callback(record: dict) 은 status='scheduled' AND scheduled_at <= now 인 각 항목에 대해
호출됨. callback 내부에서 record 의 status 를 'uploading' 으로 바꾸고 백그라운드
업로드 스레드를 띄우는 책임을 진다(=호출 측 web/app.py).

설계 메모
─────────
- 한 인터벌 안에서 due 가 여러 건이면 모두 dispatch (콜백은 즉시 thread 로 위임할 것)
- 콜백 내부 예외는 잡아서 record 를 failed 로 표시하지 않음 → 호출 측 책임
  (스케줄러는 트리거만, 결과 추적은 _run_youtube_upload 가 함)
- daemon=True 라 메인 프로세스 종료 시 같이 죽음
- 멀티 worker 환경에서 중복 실행 방지를 위해 dispatch 직전에 store 의 상태를
  'uploading' 으로 atomic 갱신 (콜백 책임). 즉 callback 은 멱등이어야 함.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from pipeline import upload_store


_started = False
_lock = threading.Lock()


def _loop(callback: Callable[[dict], None], interval_s: int) -> None:
    while True:
        try:
            due = upload_store.list_due()
            for rec in due:
                # double-check after re-read (race: 이미 다른 인터벌이 처리?)
                fresh = upload_store.get(rec["id"])
                if fresh is None or fresh.get("status") != "scheduled":
                    continue
                # 'uploading' 으로 atomic 마킹 후 콜백 위임
                marked = upload_store.update(
                    rec["id"], status="uploading"
                )
                if marked is None:
                    continue
                try:
                    callback(marked)
                except Exception as e:                              # noqa: BLE001
                    upload_store.mark_failed(rec["id"], error=f"dispatch_error: {e}")
        except Exception as e:                                       # noqa: BLE001
            print(f"[scheduler] loop error: {e}")
        time.sleep(interval_s)


def start(callback: Callable[[dict], None], *, interval_s: int = 60) -> None:
    """워커 데몬 1회 기동. 멱등 — 두 번째 호출은 무시."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    t = threading.Thread(
        target=_loop,
        args=(callback, interval_s),
        name="upload-scheduler",
        daemon=True,
    )
    t.start()
