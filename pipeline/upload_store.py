"""업로드 레코드 영속 저장소 (JSON 파일 기반).

- 파일: data/uploads.json
- 동시성: threading.RLock 으로 read-modify-write 직렬화
- 원자적 쓰기: tempfile + os.replace
- 시간대: KST(UTC+9) ISO 8601 문자열
- 멀티 worker 환경에선 동작 보장 안 됨 → uvicorn `--workers 1` 강제

레코드 라이프사이클
─────────────────────────
draft (잠재) → scheduled → uploading → done
                       ↘ cancelled
                        ↘ failed (재시도 시 scheduled 로 되돌림)
즉시 업로드: scheduled 단계 스킵 (status="uploading" 으로 바로 시작)

스키마는 모듈 상단의 SCHEMA_VERSION 으로 버저닝. 향후 마이그레이션 시
load_all() 진입점에서 한 번 처리.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import config


SCHEMA_VERSION = 1
KST = timezone(timedelta(hours=9))

_DATA_DIR  = config.BASE_DIR / "data"
_STORE     = _DATA_DIR / "uploads.json"
_LOCK      = threading.RLock()


# ── Time helpers ─────────────────────────────────────────────────────────────

def now_iso() -> str:
    """현재 시각을 KST ISO 8601 문자열로."""
    return datetime.now(KST).isoformat(timespec="seconds")


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt


# ── File I/O (locked) ────────────────────────────────────────────────────────

def _empty_store() -> dict:
    return {"schema_version": SCHEMA_VERSION, "items": []}


def _read_raw() -> dict:
    if not _STORE.exists():
        return _empty_store()
    try:
        with _STORE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _empty_store()
    if not isinstance(data, dict):
        return _empty_store()
    data.setdefault("schema_version", SCHEMA_VERSION)
    if not isinstance(data.get("items"), list):
        data["items"] = []
    return data


def _write_raw(data: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="uploads_", suffix=".json.tmp", dir=str(_DATA_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _STORE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Record factory ───────────────────────────────────────────────────────────

def _new_id() -> str:
    return "u_" + uuid.uuid4().hex[:8]


def _make_record(
    *,
    filename: str,
    title: str,
    description: str = "",
    tags: Optional[list[str]] = None,
    category_id: str = "28",
    privacy_status: str = "private",
    made_for_kids: bool = False,
    scheduled_at: Optional[str] = None,
    status: str = "scheduled",
    job_id: Optional[str] = None,
) -> dict:
    ts = now_iso()
    return {
        "id":             _new_id(),
        "filename":       filename,
        "title":          title,
        "description":    description,
        "tags":           list(tags or []),
        "category_id":    category_id,
        "privacy_status": privacy_status,
        "made_for_kids":  bool(made_for_kids),
        "scheduled_at":   scheduled_at,
        "status":         status,
        "video_id":       None,
        "video_url":      None,
        "uploaded_at":    None,
        "error":          None,
        "progress":       0,
        "stats":          None,
        "stats_fetched_at": None,
        "telegram_notified": False,
        "job_id":         job_id,
        "created_at":     ts,
        "updated_at":     ts,
    }


# ── Public API ───────────────────────────────────────────────────────────────

def add(**kwargs: Any) -> dict:
    """새 레코드 생성 + 저장. 생성된 record(dict) 반환."""
    rec = _make_record(**kwargs)
    with _LOCK:
        data = _read_raw()
        data["items"].append(rec)
        _write_raw(data)
    return rec


def get(record_id: str) -> Optional[dict]:
    with _LOCK:
        data = _read_raw()
    for it in data["items"]:
        if it.get("id") == record_id:
            return it
    return None


def list_all() -> list[dict]:
    with _LOCK:
        data = _read_raw()
    return list(data["items"])


def list_by_status(status: str) -> list[dict]:
    return [it for it in list_all() if it.get("status") == status]


def list_due(now: Optional[datetime] = None) -> list[dict]:
    """status == 'scheduled' AND scheduled_at <= now 인 항목들 반환."""
    now = now or datetime.now(KST)
    out: list[dict] = []
    for it in list_all():
        if it.get("status") != "scheduled":
            continue
        sched = parse_iso(it.get("scheduled_at"))
        if sched is None:
            continue
        if sched <= now:
            out.append(it)
    return out


def update(record_id: str, **patch: Any) -> Optional[dict]:
    """레코드 부분 갱신. 존재하지 않으면 None.

    patch 내 'updated_at' 은 자동으로 덮어쓰기됨.
    """
    with _LOCK:
        data = _read_raw()
        target: Optional[dict] = None
        for it in data["items"]:
            if it.get("id") == record_id:
                target = it
                break
        if target is None:
            return None
        for k, v in patch.items():
            target[k] = v
        target["updated_at"] = now_iso()
        _write_raw(data)
        return dict(target)


def delete(record_id: str) -> bool:
    """레코드 영구 삭제. 성공 시 True."""
    with _LOCK:
        data = _read_raw()
        before = len(data["items"])
        data["items"] = [it for it in data["items"] if it.get("id") != record_id]
        if len(data["items"]) == before:
            return False
        _write_raw(data)
        return True


def mark_uploading_as_failed_on_startup() -> int:
    """서버 재시작 직후 호출 — uploading 상태로 끊긴 항목을 failed 로 마킹.

    반환: 마킹한 건수.
    """
    with _LOCK:
        data = _read_raw()
        n = 0
        for it in data["items"]:
            if it.get("status") == "uploading":
                it["status"] = "failed"
                it["error"]  = it.get("error") or "server_restart_interrupted"
                it["updated_at"] = now_iso()
                n += 1
        if n:
            _write_raw(data)
    return n


# ── Convenience wrappers for the common UI flows ────────────────────────────

def add_immediate(
    *,
    filename: str,
    title: str,
    description: str = "",
    tags: Optional[list[str]] = None,
    category_id: str = "28",
    privacy_status: str = "private",
    made_for_kids: bool = False,
    job_id: Optional[str] = None,
) -> dict:
    """'지금 업로드' 트리거 시점의 레코드 생성 (status=uploading)."""
    return add(
        filename=filename,
        title=title,
        description=description,
        tags=tags,
        category_id=category_id,
        privacy_status=privacy_status,
        made_for_kids=made_for_kids,
        scheduled_at=None,
        status="uploading",
        job_id=job_id,
    )


def add_scheduled(
    *,
    filename: str,
    title: str,
    scheduled_at: str,
    description: str = "",
    tags: Optional[list[str]] = None,
    category_id: str = "28",
    privacy_status: str = "private",
    made_for_kids: bool = False,
) -> dict:
    """예약 등록."""
    return add(
        filename=filename,
        title=title,
        description=description,
        tags=tags,
        category_id=category_id,
        privacy_status=privacy_status,
        made_for_kids=made_for_kids,
        scheduled_at=scheduled_at,
        status="scheduled",
    )


def mark_done(record_id: str, *, video_id: str, video_url: str) -> Optional[dict]:
    return update(
        record_id,
        status="done",
        video_id=video_id,
        video_url=video_url,
        uploaded_at=now_iso(),
        progress=100,
        error=None,
    )


def mark_failed(record_id: str, *, error: str) -> Optional[dict]:
    return update(record_id, status="failed", error=error)


def mark_cancelled(record_id: str) -> Optional[dict]:
    return update(record_id, status="cancelled")


def update_progress(record_id: str, progress: int) -> Optional[dict]:
    return update(record_id, progress=int(max(0, min(100, progress))))


def find_by_job_id(job_id: str) -> Optional[dict]:
    for it in list_all():
        if it.get("job_id") == job_id:
            return it
    return None
