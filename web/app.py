import sys
import uuid
import json
import threading
import asyncio
import re
import hmac
import hashlib
import shutil
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import config

app = FastAPI(title="힙포인사이트 쇼츠 생성기")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/output", StaticFiles(directory=str(config.OUTPUT_DIR)), name="output")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

jobs: dict[str, dict] = {}


def _auth_enabled() -> bool:
    return bool((config.APP_PASSWORD or "").strip())


def _auth_token() -> str:
    secret = (config.AUTH_COOKIE_SECRET or config.APP_PASSWORD or "").encode("utf-8")
    password = (config.APP_PASSWORD or "").encode("utf-8")
    return hmac.new(secret, password, hashlib.sha256).hexdigest()


def _is_authenticated(request: Request) -> bool:
    if not _auth_enabled():
        return True
    token = request.cookies.get("hippoinst_auth") or ""
    return hmac.compare_digest(token, _auth_token())


def _login_page(error: str = "") -> HTMLResponse:
    message = f'<p class="error">{error}</p>' if error else ""
    return HTMLResponse(f"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>힙포인사이트 로그인</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center;
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background:#111; color:#f7f7f7; }}
  form {{ width:min(360px, calc(100vw - 32px)); display:grid; gap:14px; }}
  h1 {{ margin:0 0 8px; font-size:24px; }}
  input, button {{ box-sizing:border-box; width:100%; height:44px; border-radius:8px;
    border:1px solid #444; font-size:16px; }}
  input {{ padding:0 12px; background:#191919; color:#fff; }}
  button {{ border:0; background:#f0c040; color:#171306; font-weight:700; cursor:pointer; }}
  .error {{ margin:0; color:#ff8d8d; font-size:14px; }}
</style>
</head><body>
<form method="post" action="/login">
  <h1>힙포인사이트</h1>
  {message}
  <input name="password" type="password" autocomplete="current-password" placeholder="APP_PASSWORD" autofocus>
  <button type="submit">접속</button>
</form>
</body></html>""")


@app.middleware("http")
async def _password_gate(request: Request, call_next):
    path = request.url.path
    if not _auth_enabled() or path in ("/login", "/favicon.ico") or path.startswith("/static/"):
        return await call_next(request)
    if _is_authenticated(request):
        return await call_next(request)
    if path.startswith("/api/"):
        return HTMLResponse("Unauthorized", status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_form():
    if not _auth_enabled():
        return RedirectResponse("/", status_code=303)
    return _login_page()


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    password = str(form.get("password") or "")
    if not hmac.compare_digest(password.encode("utf-8"), config.APP_PASSWORD.encode("utf-8")):
        return _login_page("비밀번호가 맞지 않습니다.")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        "hippoinst_auth",
        _auth_token(),
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="lax",
    )
    return resp


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


# ── Request models ────────────────────────────────────────────────────────────

class ScriptRequest(BaseModel):
    articles: list[str] = []
    title: Optional[str] = None


class TTSPreviewRequest(BaseModel):
    provider: str = "typecast"       # elevenlabs | typecast
    voice_id: Optional[str] = None
    text:     str = "안녕하세요, 힙포인사이트입니다. 오늘도 흥미로운 AI 소식을 들고 왔어요."


class TTSDurationRequest(BaseModel):
    """렌더 전 TTS 총 길이 추정 / 측정용."""
    narration: list[str] | str = ""
    provider:  str = "typecast"      # elevenlabs | typecast
    voice_id:  Optional[str] = None


class NewsSearchRequest(BaseModel):
    topic: str = "robot"          # robot | ai
    limit: int = 7                # 5~8 권장


class YouTubeUploadRequest(BaseModel):
    filename:       str
    title:          str
    description:    str = ""
    tags:           list[str] = []
    category_id:    str = "28"          # Science & Technology
    privacy_status: str = "private"     # private | unlisted | public
    made_for_kids:  bool = False


class ScheduleUploadRequest(YouTubeUploadRequest):
    """예약 업로드 등록 요청. scheduled_at 은 KST ISO 8601 (예: 2026-05-02T08:00:00+09:00).

    HTML5 datetime-local 의 'YYYY-MM-DDTHH:MM' 도 허용 — 파싱 시 KST 로 가정.
    """
    scheduled_at: str


class UploadPatchRequest(BaseModel):
    """예약된 업로드 부분 수정. None 인 필드는 변경하지 않음."""
    title:          Optional[str]       = None
    description:    Optional[str]       = None
    tags:           Optional[list[str]] = None
    category_id:    Optional[str]       = None
    privacy_status: Optional[str]       = None
    made_for_kids:  Optional[bool]      = None
    scheduled_at:   Optional[str]       = None


class PreviewClipRequest(BaseModel):
    url:   str
    start: str   # mm:ss / hh:mm:ss / 초
    end:   str


class MultiRenderRequest(BaseModel):
    clips:       list[dict]      # [{url, start, end}]  (2~5)
    transitions: list[str]       # 길이 = clips-1
    bgm:         str = "bgm_light"
    pill:        str = ""
    hook:        str = ""
    provider:    str = "typecast"       # elevenlabs | typecast
    voice_id:    str = config.TYPECAST_VOICE_ID
    script:      Optional[dict] = None   # final edited script from the UI
    confirmed_tts_id: Optional[str] = None
    hook_accent_color: str = config.HOOK_ACCENT_COLOR_DEFAULT  # 훅 두번째줄 색상 (#RRGGBB)
    subtitle_color: str = "#FFFFFF"      # 자막 강조 색상 (#RRGGBB)
    bg_template: str = "bg_purple"


class RenderRequest(BaseModel):
    articles: list[str] = []
    urls: list[str] = []
    start_times: list[str] = ["00:00:00", "00:00:00"]
    duration: int = 55
    bgm: str = "bgm_light"
    provider: str = "typecast"       # elevenlabs | typecast
    voice_id: str = config.TYPECAST_VOICE_ID
    script: Optional[dict] = None    # pre-generated or manually edited
    pill: str = ""                   # 노란 알약 부제 (선택)
    hook_accent_color: str = config.HOOK_ACCENT_COLOR_DEFAULT  # 훅 두번째줄 색상 (#RRGGBB)
    subtitle_color: str = "#FFFFFF"  # 자막 강조 색상 (#RRGGBB)
    bg_template: str = "bg_purple"


class ApiKeyUpdateRequest(BaseModel):
    provider: str   # "typecast" | "elevenlabs"
    api_key: str


class SingleClipRenderRequest(BaseModel):
    clip_url:         str
    clip_start:       str = "00:00:00"
    clip_end:         str = "00:00:10"
    clip_volume:      float = 1.0           # 0.0~2.0 (UI 0~200%)
    use_tts:          bool = True
    bgm:              str = "bgm_light"
    bg_template:      str = "bg_purple"
    pill:             str = ""
    hook:             str = ""
    provider:         str = "typecast"
    voice_id:         str = config.TYPECAST_VOICE_ID
    script:           Optional[dict] = None
    confirmed_tts_id: Optional[str] = None
    hook_accent_color: str = config.HOOK_ACCENT_COLOR_DEFAULT
    subtitle_color:   str = "#FFFFFF"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "default_voice_id":          config.ELEVENLABS_VOICE_ID,
            "default_typecast_voice_id": config.TYPECAST_VOICE_ID,
            "tts_voices":                config.TTS_UI_VOICES,
            "bgm_options":               [k for k, p in config.BGM_MAP.items() if Path(str(p)).exists()],
            "bg_template_options":       [k for k, p in config.BG_TEMPLATE_MAP.items() if Path(str(p)).exists()],
            "api_key_hints": {
                "typecast":   _mask_key(config.TYPECAST_API_KEY),
                "elevenlabs": _mask_key(config.ELEVENLABS_API_KEY),
            },
        },
    )


@app.post("/api/generate-script")
async def generate_script_api(req: ScriptRequest):
    try:
        from pipeline.script_generator import generate_two_variants_from_articles

        articles = [a.strip() for a in req.articles if a.strip()]
        if not articles:
            return {"error": "기사를 입력해주세요"}
        result = generate_two_variants_from_articles(articles)
        return {
            "scripts":  result["scripts"],
            "used":     result.get("used") or [],
            "warnings": result.get("warnings") or [],
        }
    except ModuleNotFoundError as e:
        return {"error": f"서버 의존성이 설치되지 않았어요: {e.name}"}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/news-search")
async def news_search_api(req: NewsSearchRequest):
    from pipeline.news_search import search_news
    try:
        items = search_news(req.topic, limit=req.limit)
        return {"items": items}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/youtube-upload")
async def youtube_upload_api(req: YouTubeUploadRequest):
    """즉시 업로드 트리거 — store 에 record 생성 후 백그라운드 실행."""
    from pipeline import upload_store

    file_path = config.OUTPUT_DIR / req.filename
    if not file_path.exists():
        return {"error": f"파일을 찾을 수 없어요: {req.filename}"}
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "status": "queued", "progress": 0,
        "message": "YouTube 업로드 준비 중...", "output": None, "error": None,
    }
    rec = upload_store.add_immediate(
        filename=req.filename,
        title=req.title,
        description=req.description,
        tags=req.tags,
        category_id=req.category_id,
        privacy_status=req.privacy_status,
        made_for_kids=req.made_for_kids,
        job_id=job_id,
    )
    threading.Thread(
        target=_run_youtube_upload,
        args=(job_id, file_path, req),
        kwargs={"record_id": rec["id"]},
        daemon=True,
    ).start()
    return {"job_id": job_id, "record_id": rec["id"]}


# ── Upload store read APIs (Phase 2) ─────────────────────────────────────────

@app.get("/api/uploads")
async def list_uploads_api(status: Optional[str] = None):
    """업로드 레코드 전체 리스트. ?status=scheduled|uploading|done|failed|cancelled 로 필터."""
    from pipeline import upload_store
    items = upload_store.list_by_status(status) if status else upload_store.list_all()
    items_sorted = sorted(
        items,
        key=lambda it: it.get("scheduled_at") or it.get("created_at") or "",
        reverse=True,
    )
    return {"items": items_sorted, "count": len(items_sorted)}


@app.get("/api/uploads/{record_id}")
async def get_upload_api(record_id: str):
    from pipeline import upload_store
    rec = upload_store.get(record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="record not found")
    return rec


@app.get("/api/output-files")
async def list_output_files_api():
    """output/ 안의 mp4 파일 리스트 (관리 탭의 새 예약 등록 폼에서 사용)."""
    out_dir = config.OUTPUT_DIR
    if not out_dir.exists():
        return {"files": []}
    files = []
    for p in out_dir.glob("*.mp4"):
        try:
            stat = p.stat()
        except OSError:
            continue
        files.append({
            "filename": p.name,
            "size":     stat.st_size,
            "mtime":    stat.st_mtime,
        })
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return {"files": files}


def _normalize_scheduled_at(raw: str) -> str:
    """HTML5 datetime-local('YYYY-MM-DDTHH:MM') 또는 ISO 8601 입력을 KST tz-aware ISO 로 정규화.

    - 'YYYY-MM-DDTHH:MM' / 'YYYY-MM-DDTHH:MM:SS' (tz 없음) → KST 로 가정
    - 이미 tz 가 박혀있으면 그대로 유지
    """
    from pipeline.upload_store import KST, parse_iso

    if not raw or not isinstance(raw, str):
        raise ValueError("scheduled_at 이 비어있어요")
    s = raw.strip()
    dt = parse_iso(s)
    if dt is None:
        raise ValueError(f"scheduled_at 형식을 인식할 수 없어요: {raw}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.isoformat(timespec="seconds")


@app.post("/api/uploads/schedule")
async def schedule_upload_api(req: ScheduleUploadRequest):
    """예약 업로드 등록 — 영상 제작 탭 / 관리 탭 양쪽에서 호출."""
    from pipeline import upload_store

    file_path = config.OUTPUT_DIR / req.filename
    if not file_path.exists():
        return {"error": f"파일을 찾을 수 없어요: {req.filename}"}
    try:
        scheduled_iso = _normalize_scheduled_at(req.scheduled_at)
    except ValueError as e:
        return {"error": str(e)}

    rec = upload_store.add_scheduled(
        filename=req.filename,
        title=req.title,
        description=req.description,
        tags=req.tags,
        category_id=req.category_id,
        privacy_status=req.privacy_status,
        made_for_kids=req.made_for_kids,
        scheduled_at=scheduled_iso,
    )
    return {"record": rec}


@app.post("/api/uploads/{record_id}/upload-now")
async def upload_now_api(record_id: str):
    """예약 항목을 즉시 업로드로 트리거 (또는 failed 항목 재시도)."""
    from pipeline import upload_store

    rec = upload_store.get(record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="record not found")
    if rec.get("status") not in ("scheduled", "failed", "cancelled"):
        return {"error": f"현재 상태({rec.get('status')})에서는 즉시 업로드할 수 없어요"}

    file_path = config.OUTPUT_DIR / rec["filename"]
    if not file_path.exists():
        upload_store.mark_failed(record_id, error=f"파일 없음: {rec['filename']}")
        return {"error": f"파일을 찾을 수 없어요: {rec['filename']}"}

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "status": "queued", "progress": 0,
        "message": "YouTube 업로드 준비 중...", "output": None, "error": None,
    }
    upload_store.update(record_id, status="uploading", job_id=job_id, error=None)
    req = YouTubeUploadRequest(
        filename=rec["filename"],
        title=rec["title"],
        description=rec.get("description") or "",
        tags=rec.get("tags") or [],
        category_id=rec.get("category_id") or "28",
        privacy_status=rec.get("privacy_status") or "private",
        made_for_kids=bool(rec.get("made_for_kids")),
    )
    threading.Thread(
        target=_run_youtube_upload,
        args=(job_id, file_path, req),
        kwargs={"record_id": record_id},
        daemon=True,
    ).start()
    return {"job_id": job_id, "record_id": record_id}


@app.patch("/api/uploads/{record_id}")
async def patch_upload_api(record_id: str, patch: UploadPatchRequest):
    """예약 항목 부분 수정 — status='scheduled' 일 때만 허용."""
    from pipeline import upload_store

    rec = upload_store.get(record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="record not found")
    if rec.get("status") != "scheduled":
        return {"error": f"현재 상태({rec.get('status')})에서는 수정할 수 없어요"}

    fields = patch.model_dump(exclude_unset=True, exclude_none=True)
    if "scheduled_at" in fields:
        try:
            fields["scheduled_at"] = _normalize_scheduled_at(fields["scheduled_at"])
        except ValueError as e:
            return {"error": str(e)}
    if not fields:
        return {"error": "변경할 항목이 없어요"}
    updated = upload_store.update(record_id, **fields)
    return {"record": updated}


@app.delete("/api/uploads/{record_id}")
async def delete_upload_api(record_id: str):
    """레코드 영구 삭제. 업로드 진행 중인 항목은 차단."""
    from pipeline import upload_store

    rec = upload_store.get(record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="record not found")
    if rec.get("status") == "uploading":
        return {"error": "업로드 진행 중인 항목은 삭제할 수 없어요"}
    upload_store.delete(record_id)
    return {"deleted": True, "id": record_id}


# ── Scheduler dispatcher (Phase 3) ───────────────────────────────────────────

def _dispatch_scheduled_upload(rec: dict) -> None:
    """폴러가 due 항목을 발견했을 때 호출되는 콜백.
    업로드 백그라운드 스레드를 띄우고 즉시 반환. record 는 이미 status='uploading' 상태.
    """
    from pipeline import upload_store

    file_path = config.OUTPUT_DIR / rec["filename"]
    if not file_path.exists():
        upload_store.mark_failed(rec["id"], error=f"파일 없음: {rec['filename']}")
        return

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "status": "queued", "progress": 0,
        "message": f"예약 업로드 시작 — {rec.get('title','')[:30]}",
        "output": None, "error": None,
    }
    upload_store.update(rec["id"], job_id=job_id)

    req = YouTubeUploadRequest(
        filename=rec["filename"],
        title=rec["title"],
        description=rec.get("description") or "",
        tags=rec.get("tags") or [],
        category_id=rec.get("category_id") or "28",
        privacy_status=rec.get("privacy_status") or "private",
        made_for_kids=bool(rec.get("made_for_kids")),
    )
    threading.Thread(
        target=_run_youtube_upload,
        args=(job_id, file_path, req),
        kwargs={"record_id": rec["id"]},
        daemon=True,
    ).start()


def _remove_old_entries(base_dir: Path, max_age_hours: int, *, pattern: str = "*") -> int:
    base_dir = Path(base_dir)
    if not base_dir.exists():
        return 0
    cutoff = time.time() - max(1, int(max_age_hours)) * 3600
    removed = 0
    for path in base_dir.glob(pattern):
        if not path.exists():
            continue
        try:
            if path.stat().st_mtime > cutoff:
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed += 1
        except Exception as exc:
            print(f"[cleanup] skip {path}: {exc}", flush=True)
    return removed


def _cleanup_runtime_once() -> None:
    removed_temp = _remove_old_entries(
        config.TEMP_DIR,
        config.TEMP_CLEANUP_MAX_AGE_HOURS,
    )
    removed_remotion = _remove_old_entries(
        config.BASE_DIR / "remotion" / "public",
        config.REMOTION_JOB_CLEANUP_MAX_AGE_HOURS,
        pattern="job_*",
    )
    if removed_temp or removed_remotion:
        print(
            f"[cleanup] temp {removed_temp}개, remotion job {removed_remotion}개 정리",
            flush=True,
        )


def _start_runtime_cleanup() -> None:
    if not config.TEMP_CLEANUP_ENABLED:
        print("[cleanup] TEMP_CLEANUP_ENABLED=0 — 자동 정리 비활성", flush=True)
        return

    def worker() -> None:
        while True:
            try:
                _cleanup_runtime_once()
            except Exception as exc:
                print(f"[cleanup] failed: {exc}", flush=True)
            time.sleep(max(1, int(config.TEMP_CLEANUP_INTERVAL_HOURS)) * 3600)

    threading.Thread(target=worker, daemon=True).start()


@app.on_event("startup")
async def _startup_uploads() -> None:
    """재시작 복구 + 스케줄러/통계 폴러 시동."""
    from pipeline import upload_store, upload_scheduler, stats_poller

    _start_runtime_cleanup()
    n = upload_store.mark_uploading_as_failed_on_startup()
    if n:
        print(f"[startup] {n} 건의 끊긴 업로드를 'failed' 로 마킹했어요")
    upload_scheduler.start(_dispatch_scheduled_upload, interval_s=60)
    print("[startup] upload-scheduler 데몬 기동 (60초 주기)")
    stats_poller.start(interval_s=30 * 60)
    print("[startup] stats-poller 데몬 기동 (30분 주기)")


@app.post("/api/uploads/{record_id}/refresh-stats")
async def refresh_stats_api(record_id: str):
    """단일 항목 통계 강제 갱신."""
    from pipeline import upload_store
    from pipeline.youtube_publisher import fetch_video_stats

    rec = upload_store.get(record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="record not found")
    if rec.get("status") != "done" or not rec.get("video_id"):
        return {"error": "업로드가 완료된 항목만 통계를 가져올 수 있어요"}

    try:
        stats_map = await asyncio.to_thread(fetch_video_stats, [rec["video_id"]])
    except Exception as e:
        return {"error": str(e)}
    s = stats_map.get(rec["video_id"])
    if not s:
        return {"error": "통계를 가져오지 못했어요 (영상이 비공개거나 삭제됐을 수 있어요)"}
    fetched_at = upload_store.now_iso()
    updated = upload_store.update(record_id, stats=s, stats_fetched_at=fetched_at)
    return {"record": updated}


@app.post("/api/stats/refresh-all")
async def refresh_stats_all_api():
    """전체 done 항목 통계 일괄 갱신 (관리 탭의 '🔄 통계 새로고침' 버튼)."""
    from pipeline import stats_poller
    summary = await asyncio.to_thread(stats_poller.refresh_once)
    return summary


# ── Telegram 알림 (Phase 7) ─────────────────────────────────────────────────

@app.get("/api/telegram/status")
async def telegram_status_api():
    """관리 탭 토글에서 사용 — 설정 여부와 활성 상태 반환."""
    from pipeline import notifier
    return notifier.status_summary()


@app.post("/api/uploads/{record_id}/notify")
async def notify_record_api(record_id: str):
    """수동 재전송 — 이미 발송된 항목도 다시 보냄."""
    from pipeline import upload_store, notifier

    rec = upload_store.get(record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="record not found")
    if not notifier.is_enabled():
        return {"error": "Telegram 알림이 비활성화 상태예요 (설정 또는 자동비활성)"}

    if rec.get("status") == "done":
        ok = await asyncio.to_thread(notifier.notify_upload_success, rec)
    elif rec.get("status") == "failed":
        ok = await asyncio.to_thread(notifier.notify_upload_failed, rec)
    else:
        return {"error": f"현재 상태({rec.get('status')})는 알림 대상이 아니에요"}
    if ok:
        upload_store.update(record_id, telegram_notified=True)
        return {"sent": True}
    return {"error": "Telegram 발송 실패 (서버 로그 확인)"}


@app.post("/api/config/api-key")
async def update_api_key(req: ApiKeyUpdateRequest):
    import tempfile
    import os as _os

    key_map = {"typecast": "TYPECAST_API_KEY", "elevenlabs": "ELEVENLABS_API_KEY"}
    if req.provider not in key_map:
        return {"error": "provider 값이 올바르지 않아요 (typecast | elevenlabs)"}

    env_var = key_map[req.provider]
    env_path = config.ENV_FILE

    try:
        lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except FileNotFoundError:
        lines = []

    new_line = f"{env_var}={req.api_key}\n"
    found = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(env_var + "="):
            lines[i] = new_line
            found = True
            break
    if not found:
        lines.append(new_line)

    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(env_path.parent), suffix=".tmp")
    try:
        with _os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.writelines(lines)
        _os.replace(tmp_path, str(env_path))
    except Exception as e:
        try:
            _os.unlink(tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

    _os.environ[env_var] = req.api_key
    setattr(config, env_var, req.api_key)

    return {"ok": True, "provider": req.provider, "masked": _mask_key(req.api_key)}


@app.post("/api/tts-preview")
async def tts_preview_api(req: TTSPreviewRequest):
    """짧은 샘플 텍스트로 미리듣기용 MP3 생성."""
    from pipeline.tts import synthesize_preview
    try:
        text = (req.text or "").strip()[:200] or \
               "안녕하세요, 힙포인사이트입니다."
        out = synthesize_preview(text, provider=req.provider, voice_id=req.voice_id)
        return FileResponse(str(out), media_type="audio/mpeg",
                            headers={"Cache-Control": "no-cache"})
    except Exception as e:
        return {"error": str(e)}


def _normalize_narration_lines(narration) -> list[str]:
    if isinstance(narration, str):
        return [ln.strip() for ln in narration.splitlines() if ln.strip()]
    return [str(ln).strip() for ln in (narration or []) if str(ln).strip()]


@app.post("/api/tts-duration/estimate")
async def tts_duration_estimate_api(req: TTSDurationRequest):
    """글자수 기반 즉석 추정. 캐시에 측정값 있으면 같이 반환. API 호출 없음."""
    import math
    from pipeline.tts import estimate_tts_duration, lookup_cached_tts_duration

    lines     = _normalize_narration_lines(req.narration)
    estimated = estimate_tts_duration(lines)
    measured  = lookup_cached_tts_duration(
        lines, provider=req.provider, voice_id=req.voice_id,
    )
    char_count = sum(len(ln) for ln in lines)
    primary = measured if measured is not None else estimated
    video_total = max(0, int(math.ceil(primary)) + 2) if primary > 0 else 0
    return {
        "estimated":   round(estimated, 2),
        "measured":    None if measured is None else round(measured, 2),
        "video_total": video_total,
        "char_count":  char_count,
        "cache_hit":   measured is not None,
    }


@app.post("/api/tts-duration/measure")
async def tts_duration_measure_api(req: TTSDurationRequest):
    """실 TTS 합성 (또는 캐시 hit) 후 정확한 duration 반환. API 비용 발생 가능."""
    import math
    from pipeline.tts import generate_tts, lookup_cached_tts_duration

    lines = _normalize_narration_lines(req.narration)
    if not lines:
        return {"error": "나레이션이 비어있어요"}

    cached_before = lookup_cached_tts_duration(
        lines, provider=req.provider, voice_id=req.voice_id,
    )
    try:
        _, duration, _ = await asyncio.to_thread(
            generate_tts,
            lines,
            provider=req.provider,
            voice_id=req.voice_id,
        )
    except Exception as e:
        return {"error": str(e)}
    return {
        "measured":    round(float(duration), 2),
        "video_total": max(1, int(math.ceil(duration)) + 2),
        "cache_hit":   cached_before is not None,
    }


@app.post("/api/tts-confirm")
async def tts_confirm_api(req: TTSDurationRequest):
    """실제 전체 TTS를 확정 생성하고 미리듣기 URL을 반환."""
    import math
    from pipeline.tts import generate_tts, tts_cache_audio_path, tts_cache_id

    lines = _normalize_narration_lines(req.narration)
    if not lines:
        return {"error": "나레이션이 비어있어요"}

    tts_id = tts_cache_id(lines, provider=req.provider, voice_id=req.voice_id)
    try:
        cached_before = tts_cache_audio_path(tts_id) is not None
    except ValueError:
        cached_before = False

    try:
        _, duration, _ = await asyncio.to_thread(
            generate_tts,
            lines,
            provider=req.provider,
            voice_id=req.voice_id,
        )
    except Exception as e:
        return {"error": str(e)}

    try:
        audio_path = tts_cache_audio_path(tts_id)
    except ValueError as e:
        return {"error": str(e)}
    if audio_path is None:
        return {"error": "TTS는 생성됐지만 캐시 저장에 실패했어요. 다시 시도해주세요"}

    return {
        "tts_id":      tts_id,
        "measured":    round(float(duration), 2),
        "video_total": max(1, int(math.ceil(duration)) + 2),
        "audio_url":   f"/api/tts-confirm/{tts_id}.mp3",
        "cache_hit":   cached_before,
    }


@app.get("/api/tts-confirm/{tts_id}.mp3")
async def tts_confirm_audio(tts_id: str):
    """확정 TTS 캐시 MP3 반환."""
    from pipeline.tts import tts_cache_audio_path

    try:
        audio_path = tts_cache_audio_path(tts_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if audio_path is None:
        raise HTTPException(status_code=404, detail="TTS 캐시를 찾을 수 없어요")
    return FileResponse(
        str(audio_path),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-cache"},
    )


@app.post("/api/render")
async def render_api(req: RenderRequest):
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "status": "queued", "progress": 0,
        "message": "준비 중...", "output": None, "error": None,
    }
    threading.Thread(target=_run_pipeline, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/progress/{job_id}")
async def progress_stream(job_id: str):
    async def event_gen():
        while True:
            job = jobs.get(job_id, {"status": "error", "message": "job not found"})
            yield f"data: {json.dumps(job, ensure_ascii=False)}\n\n"
            if job.get("status") in ("done", "error"):
                break
            await asyncio.sleep(0.5)
    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/download/{filename}")
async def download(filename: str):
    path = config.OUTPUT_DIR / filename
    if not path.exists():
        return {"error": "File not found"}
    return FileResponse(str(path), media_type="video/mp4", filename=filename)


@app.get("/api/_debug/character")
async def _debug_character():
    """디버그 — 서버 컨텍스트의 character 모듈 상태 점검."""
    import sys
    info: dict = {"python": sys.executable, "version": sys.version.split()[0]}
    try:
        from pipeline import character
        info["module_path"]   = character.__file__
        info["LIBROSA_OK"]    = character._LIBROSA_OK
        info["IMAGEIO_OK"]    = character._IMAGEIO_OK
        info["NUMPY_OK"]      = character._NUMPY_OK
        info["is_available"]  = character.is_available()
        info["CHARACTER_DIR"] = str(config.CHARACTER_DIR)
        info["dir_exists"]    = config.CHARACTER_DIR.exists()
        from pipeline.character import MOUTH_FILES
        info["mouth_files"]   = {n: (config.CHARACTER_DIR / fn).exists() for n, fn in MOUTH_FILES.items()}
    except Exception as e:
        info["error"] = repr(e)
    return info


def _add_audio_to_preview(url: str, start: float, end: float, video_path) -> None:
    """미리보기 mp4에 오디오 트랙이 없으면 yt-dlp로 추가한다."""
    import subprocess
    from pathlib import Path as _P
    vp = _P(video_path)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(vp)],
        capture_output=True, text=True, timeout=10
    )
    if probe.stdout.strip():
        return  # 이미 오디오 있음
    section = f"*{start:.2f}-{end:.2f}"
    audio_base = vp.parent / f"{vp.stem}_tmpa"
    merged = vp.parent / f"{vp.stem}_mg.mp4"
    try:
        subprocess.run(
            ["yt-dlp", "--no-continue", "--force-overwrites",
             "--socket-timeout", "15", "--retries", "2", "--fragment-retries", "2",
             "--download-sections", section,
             "-f", "bestaudio[ext=m4a]/bestaudio",
             "-o", str(audio_base) + ".%(ext)s", url],
            capture_output=True, text=True, timeout=90
        )
        audio_files = list(vp.parent.glob(f"{vp.stem}_tmpa.*"))
        if not audio_files:
            return
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-i", str(vp), "-i", str(audio_files[0]),
             "-c:v", "copy", "-c:a", "aac", str(merged)],
            capture_output=True, text=True, timeout=60
        )
        if r.returncode == 0 and merged.exists() and merged.stat().st_size > 0:
            merged.replace(vp)
    except Exception:
        pass
    finally:
        for f in vp.parent.glob(f"{vp.stem}_tmpa.*"):
            f.unlink(missing_ok=True)
        if merged.exists():
            merged.unlink(missing_ok=True)


@app.post("/api/preview-clip")
async def preview_clip_api(req: PreviewClipRequest):
    from pipeline.multiclip import normalize_media_url, parse_time, prepare_preview
    try:
        url = normalize_media_url(req.url)
        start = parse_time(req.start)
        end   = parse_time(req.end)
        if end <= start:
            return {"error": "종료 시간이 시작보다 커야 해요"}
        if end - start > 60:
            return {"error": "한 컷 최대 60초까지"}
        info = await asyncio.to_thread(prepare_preview, url, start, end)
        await asyncio.to_thread(_add_audio_to_preview, url, start, end, info["video"])
        return {
            "clip_id":   info["clip_id"],
            "duration":  info["duration"],
            "video_url": f"/api/preview-asset/{info['clip_id']}/video",
            "thumb_url": None if info.get("thumb") is None else f"/api/preview-asset/{info['clip_id']}/thumb",
        }
    except ValueError as e:
        return {"error": str(e)}
    except RuntimeError as e:
        if "yt-dlp" in str(e):
            return {"error": "미리보기 실패: 영상을 가져오지 못했어요. URL, 공개 여부, 구간 시간을 확인해주세요."}
        return {"error": f"미리보기 실패: {e}"}
    except Exception as e:
        return {"error": f"미리보기 실패: {e}"}


@app.post("/api/preview-clip2")
async def preview_clip2_api(req: PreviewClipRequest):
    from pipeline.multiclip import normalize_media_url, parse_time, prepare_preview2
    try:
        url = normalize_media_url(req.url)
        start = parse_time(req.start)
        end = parse_time(req.end)
        if end <= start:
            return {"error": "End time must be greater than start time."}
        if end - start > 60:
            return {"error": "Clip sections can be at most 60 seconds."}
        info = await asyncio.to_thread(prepare_preview2, url, start, end)
        return {
            "clip_id": info["clip_id"],
            "duration": info["duration"],
            "video_url": f"/api/preview2-asset/{info['clip_id']}/video",
            "thumb_url": None if info.get("thumb") is None else f"/api/preview2-asset/{info['clip_id']}/thumb",
            "has_audio": bool(info.get("has_audio")),
        }
    except ValueError as e:
        return {"error": str(e)}
    except RuntimeError as e:
        message = str(e)
        if "yt-dlp" in message:
            message = "Preview2 download failed. Please check the URL, visibility, and section times."
        return {"error": message}
    except Exception as e:
        return {"error": f"Preview2 failed: {e}"}


@app.get("/api/preview2-asset/{clip_id}/{kind}")
async def preview2_asset(clip_id: str, kind: str):
    from pipeline.multiclip import PREVIEW2_DIR
    safe = "".join(c for c in clip_id if c.isalnum())[:20]
    if kind == "video":
        path = PREVIEW2_DIR / f"{safe}.mp4"
        media = "video/mp4"
    elif kind == "thumb":
        path = PREVIEW2_DIR / f"{safe}.jpg"
        media = "image/jpeg"
    else:
        return {"error": "kind must be video or thumb"}
    if not path.exists():
        return {"error": "asset not found"}
    return FileResponse(str(path), media_type=media,
                        headers={"Cache-Control": "public, max-age=300"})


@app.get("/api/preview-asset/{clip_id}/{kind}")
async def preview_asset(clip_id: str, kind: str):
    from pipeline.multiclip import PREVIEW_DIR
    safe = "".join(c for c in clip_id if c.isalnum())[:20]
    if kind == "video":
        path = PREVIEW_DIR / f"{safe}.mp4"
        media = "video/mp4"
    elif kind == "thumb":
        path = PREVIEW_DIR / f"{safe}.jpg"
        media = "image/jpeg"
    else:
        return {"error": "kind는 video|thumb"}
    if not path.exists():
        return {"error": "asset 없음"}
    return FileResponse(str(path), media_type=media,
                        headers={"Cache-Control": "public, max-age=300"})


@app.post("/api/render-multi")
async def render_multi_api(req: MultiRenderRequest):
    if not (1 <= len(req.clips) <= 5):
        return {"error": "클립은 1~5개"}
    if len(req.transitions) != len(req.clips) - 1:
        return {"error": f"transitions 개수가 {len(req.clips)-1}개여야 해요"}
    from pipeline.multiclip import normalize_media_url, parse_time
    for i, clip in enumerate(req.clips, 1):
        if not str(clip.get("url") or "").strip():
            return {"error": f"컷 {i}: URL을 입력해주세요"}
        try:
            clip["url"] = normalize_media_url(clip.get("url"))
        except ValueError as e:
            return {"error": f"컷 {i}: {e}"}
        try:
            start = parse_time(clip.get("start"))
            end = parse_time(clip.get("end"))
        except ValueError as e:
            return {"error": f"컷 {i}: {e}"}
        if end <= start:
            return {"error": f"컷 {i}: 종료 시간이 시작보다 커야 해요"}
        if end - start > 60:
            return {"error": f"컷 {i}: 한 컷 최대 60초까지"}
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "status": "queued", "progress": 0,
        "message": "준비 중...", "output": None, "error": None,
    }
    threading.Thread(target=_run_multi_pipeline, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


def _run_multi_pipeline(job_id: str, req: MultiRenderRequest) -> None:
    def upd(progress: int, message: str):
        jobs[job_id].update({"status": "running", "progress": progress, "message": message})

    try:
        import math
        from pipeline.multiclip import (
            parse_time, prepare_preview, multiclip_duration, compose_montage,
        )
        from pipeline.tts import generate_tts, get_confirmed_tts
        from pipeline.subtitle import generate_chunk_ass, chunk_narration
        from pipeline.editor import create_background_frame, compose_video
        from pipeline.script_generator import normalize_script_shape

        config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # 1) 스크립트 확인 + TTS 생성
        upd(8, "스크립트 확인 중...")
        script = dict(req.script or {})
        if (req.hook or "").strip():
            script["hook"] = req.hook.strip()
        script = normalize_script_shape(script)
        narration = script["narration"]
        if not narration:
            raise ValueError("나레이션을 먼저 생성하거나 입력해주세요")

        hook_text = script["hook"]

        if req.confirmed_tts_id:
            upd(18, f"확정 TTS 불러오는 중... [{req.provider}]")
            tts_path, tts_duration, words = get_confirmed_tts(
                narration,
                req.confirmed_tts_id,
                provider=req.provider,
                voice_id=req.voice_id,
            )
        else:
            upd(18, f"음성(TTS) 생성 중... [{req.provider}]")
            tts_path, tts_duration, words = generate_tts(
                narration,
                provider=req.provider,
                voice_id=req.voice_id,
            )
        video_duration = max(1, int(math.ceil(tts_duration)) + 2)

        # 2) 클립 N개 다운로드 (캐시 활용)
        downloaded: list[dict] = []
        for i, c in enumerate(req.clips, 1):
            upd(int(30 + 25 * (i - 1) / len(req.clips)),
                f"클립 {i}/{len(req.clips)} 다운로드 중...")
            start = parse_time(c.get("start", "0"))
            end   = parse_time(c.get("end", "0"))
            if end <= start:
                raise ValueError(f"클립 {i}: 종료 시간이 시작보다 커야 해요")
            info = prepare_preview(c["url"], start, end)
            downloaded.append({"path": info["video"], "duration": info["duration"]})

        clip_duration = multiclip_duration(downloaded)
        if clip_duration + 0.15 < video_duration:
            raise ValueError(
                f"클립 총 길이 {clip_duration:.1f}초가 TTS 기준 길이 {video_duration:.1f}초보다 짧아요. "
                "컷 길이를 늘려주세요."
            )

        # 3) 타이틀 블록 PNG + 무음 멀티클립 몽타주
        upd(58, "타이틀 블록 생성 중...")
        bg_path = create_background_frame(
            hook_text,
            pill_text=req.pill,
            clipless=False,
            hook_accent_color=req.hook_accent_color,
            bg_template=req.bg_template,
        )

        if len(downloaded) == 1:
            upd(64, "단일 클립 사용 중...")
            montage_path = Path(downloaded[0]["path"])
        else:
            upd(64, "멀티클립 몽타주 생성 중...")
            montage_path = config.TEMP_DIR / f"{job_id}_montage.mp4"
            compose_montage(
                clips=downloaded,
                transitions=req.transitions,
                output_path=montage_path,
            )

        # 4) 자막/GIF/BGM 준비
        upd(74, "자막 생성 중...")
        raw_subs = script.get("subtitles") or []
        chunks = [str(s["text"] if isinstance(s, dict) else s).replace(".", "").strip() for s in raw_subs]
        chunks = [c for c in chunks if c]
        if not chunks:
            chunks = chunk_narration(narration)
        ass_path = generate_chunk_ass(
            chunks, words, tts_duration,
            highlight_color=req.subtitle_color,
        )

        upd(80, "GIF 페치 중...")
        gif_records = _fetch_gifs(
            script.get("gifs") or [], video_duration=video_duration, fallback=False,
        )

        bgm_path = config.BGM_MAP.get(req.bgm, config.BGM_FALLBACK)
        if not Path(str(bgm_path)).exists():
            bgm_path = config.BGM_FALLBACK

        # 5) 최종 합성: 몽타주 + TTS + 자막 + 캐릭터 + BGM
        upd(88, "최종 영상 합성 중...")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        keyword = re.sub(r'[\\/:*?"<>|]', "", hook_text[:20]).replace(" ", "_") or "multiclip"
        out_path = config.OUTPUT_DIR / f"{ts}_{keyword}.mp4"

        provider_key = (req.provider or "elevenlabs").lower()
        voice_gain = config.TTS_VOICE_GAIN.get(provider_key, config.TTS_VOICE_GAIN["elevenlabs"])
        compose_video(
            montage_path,
            bg_path=bg_path,
            ass_path=ass_path,
            output_path=out_path,
            bgm_path=bgm_path,
            tts_path=tts_path,
            duration=video_duration,
            gifs=gif_records,
            voice_gain=voice_gain,
        )
        jobs[job_id].update({
            "status": "done", "progress": 100,
            "message": "완료!", "output": out_path.name, "error": None, "script": script,
        })
    except Exception as e:
        message = str(e)
        if "yt-dlp" in message:
            message = "영상 다운로드 실패: URL, 공개 여부, 구간 시간을 확인해주세요."
        jobs[job_id].update({
            "status": "error", "progress": 100,
            "message": message, "error": message,
        })


# ── 영상 제작2: 단일 클립 + 원본 오디오 ─────────────────────────────────────

@app.post("/api/render-single2")
async def render_single2_api(req: SingleClipRenderRequest):
    if not req.clip_url.strip():
        return {"error": "클립 URL을 입력해주세요"}
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "status": "queued", "progress": 0, "message": "준비 중...",
        "output": None, "error": None, "script": None,
    }
    threading.Thread(target=_run_single2_pipeline, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


def _run_single2_pipeline(job_id: str, req: SingleClipRenderRequest) -> None:
    def upd(progress: int, message: str):
        jobs[job_id].update({"status": "running", "progress": progress, "message": message})

    try:
        import math
        from pipeline.multiclip import parse_time, prepare_preview2
        from pipeline.tts import generate_tts, get_confirmed_tts
        from pipeline.subtitle import generate_chunk_ass, chunk_narration
        from pipeline.editor import create_background_frame, compose_video
        from pipeline.script_generator import normalize_script_shape

        config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # 1) 스크립트 + TTS
        upd(8, "스크립트 확인 중...")
        script = dict(req.script or {})
        if (req.hook or "").strip():
            script["hook"] = req.hook.strip()
        script = normalize_script_shape(script)
        hook_text = script["hook"]

        tts_path = None
        tts_duration = None
        words = []
        if req.use_tts:
            narration = script["narration"]
            if not narration:
                raise ValueError("나레이션을 먼저 생성하거나 입력해주세요")
            if req.confirmed_tts_id:
                upd(18, f"확정 TTS 불러오는 중... [{req.provider}]")
                tts_path, tts_duration, words = get_confirmed_tts(
                    narration, req.confirmed_tts_id,
                    provider=req.provider, voice_id=req.voice_id,
                )
            else:
                upd(18, f"음성(TTS) 생성 중... [{req.provider}]")
                tts_path, tts_duration, words = generate_tts(
                    narration, provider=req.provider, voice_id=req.voice_id,
                )

        # 2) 클립 다운로드 (오디오 포함)
        upd(35, "클립 다운로드 중...")
        start = parse_time(req.clip_start)
        end   = parse_time(req.clip_end)
        if end <= start:
            raise ValueError("종료 시간이 시작 시간보다 커야 해요")
        info = prepare_preview2(req.clip_url, start, end)
        clip_path = info["video"]
        clip_duration = info["duration"]

        if tts_duration is not None:
            video_duration = max(1, int(math.ceil(tts_duration)) + 2)
            if clip_duration + 0.15 < video_duration:
                raise ValueError(
                    f"클립 길이 {clip_duration:.1f}초가 TTS 기준 길이 {video_duration:.1f}초보다 짧아요"
                )
        else:
            video_duration = max(1, int(math.ceil(clip_duration)))

        # 3) 배경 PNG
        upd(55, "타이틀 블록 생성 중...")
        bg_path = create_background_frame(
            hook_text,
            pill_text=req.pill,
            clipless=False,
            hook_accent_color=req.hook_accent_color,
            bg_template=req.bg_template,
        )

        # 4) 자막
        upd(68, "자막 생성 중...")
        if req.use_tts and words:
            raw_subs = script.get("subtitles") or []
            chunks = [str(s["text"] if isinstance(s, dict) else s).replace(".", "").strip() for s in raw_subs]
            chunks = [c for c in chunks if c]
            if not chunks:
                chunks = chunk_narration(script["narration"])
            ass_path = generate_chunk_ass(
                chunks, words, tts_duration,
                highlight_color=req.subtitle_color,
            )
        else:
            ass_path = generate_chunk_ass([], [], 0, highlight_color=req.subtitle_color)

        bgm_off = str(req.bgm or "").lower() == "off"
        bgm_path = config.BGM_MAP.get(req.bgm, config.BGM_FALLBACK)
        if not Path(str(bgm_path)).exists():
            bgm_path = config.BGM_FALLBACK

        # 5) 최종 합성 (클립 오디오 포함)
        upd(80, "최종 영상 합성 중...")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        keyword = re.sub(r'[\\/:*?"<>|]', "", hook_text[:20]).replace(" ", "_") or "single2"
        out_path = config.OUTPUT_DIR / f"{ts}_{keyword}.mp4"

        provider_key = (req.provider or "typecast").lower()
        voice_gain = config.TTS_VOICE_GAIN.get(provider_key, config.TTS_VOICE_GAIN["typecast"])
        compose_video(
            clip_path,
            bg_path=bg_path,
            ass_path=ass_path,
            output_path=out_path,
            bgm_path=bgm_path,
            tts_path=tts_path,
            duration=video_duration,
            voice_gain=voice_gain,
            bgm_volume=0.0 if bgm_off else None,
            clip_volume=req.clip_volume,
        )
        jobs[job_id].update({
            "status": "done", "progress": 100,
            "message": "완료!", "output": out_path.name, "error": None, "script": script,
        })
    except Exception as e:
        message = str(e)
        if "yt-dlp" in message:
            message = "영상 다운로드 실패: URL, 공개 여부, 구간 시간을 확인해주세요."
        jobs[job_id].update({
            "status": "error", "progress": 100,
            "message": message, "error": message,
        })


@app.get("/api/template-preview")
async def template_preview(
    pill: str = "",
    hook: str = "",
    hook_accent_color: str = config.HOOK_ACCENT_COLOR_DEFAULT,
    bg_template: str = "bg_purple",
):
    from pipeline.editor import create_template_preview
    hook_text = hook.strip() or "훅 텍스트|강조 한 줄"
    pill_text = pill.strip()
    path = create_template_preview(
        hook_text,
        pill_text=pill_text,
        hook_accent_color=hook_accent_color,
        bg_template=bg_template,
    )
    return FileResponse(
        str(path),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/template-preview-window", response_class=HTMLResponse)
async def template_preview_window(
    pill: str = "",
    hook: str = "",
    hook_accent_color: str = config.HOOK_ACCENT_COLOR_DEFAULT,
    bg_template: str = "bg_purple",
    t: str = "",
):
    """모바일 폰 크기 새 창으로 띄우는 PNG 뷰어 페이지."""
    from urllib.parse import urlencode
    qs = urlencode(
        {
            "pill": pill,
            "hook": hook,
            "hook_accent_color": hook_accent_color,
            "bg_template": bg_template,
            "t": t,
        },
        encoding="utf-8",
    )
    return HTMLResponse(f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>📱 템플릿 미리보기</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  html, body {{ margin:0; padding:0; height:100%; background:#000; overflow:hidden; }}
  body {{ display:flex; align-items:center; justify-content:center; }}
  img  {{ display:block; max-width:100%; max-height:100vh; height:auto; width:auto; }}
</style>
</head><body><img src="/api/template-preview?{qs}" alt="템플릿 미리보기"></body></html>""")


@app.get("/api/bgm/{name}")
async def bgm_preview(name: str):
    path = config.BGM_MAP.get(name, config.BGM_FALLBACK)
    if not Path(str(path)).exists():
        return {"error": "BGM not found"}
    return FileResponse(str(path), media_type="audio/mpeg")


# ── Pipeline runner (background thread) ──────────────────────────────────────

def _run_pipeline(job_id: str, req: RenderRequest):
    def upd(progress: int, message: str):
        jobs[job_id].update({"status": "running", "progress": progress, "message": message})

    try:
        from pipeline.downloader import download_clip
        from pipeline.script_generator import generate_script_from_articles, normalize_script_shape
        from pipeline.tts import generate_tts
        from pipeline.subtitle import generate_chunk_ass, chunk_narration
        from pipeline.editor import create_background_frame, compose_video

        config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        url = next((u.strip() for u in req.urls if u.strip()), None)
        if url:
            upd(10, "영상 다운로드 중...")
            clip_path = download_clip(url, req.start_times[0] or "00:00:00", req.duration)
        else:
            upd(10, "참조 영상 없음 — 스크립트 베이스 모드")
            clip_path = None

        upd(25, "스크립트 생성 중...")
        if req.script:
            script = req.script
        else:
            articles = [a.strip() for a in req.articles if a.strip()]
            if not articles:
                raise ValueError("기사를 입력해주세요")
            script = generate_script_from_articles(articles)["script"]
        script = normalize_script_shape(script)

        upd(45, f"음성(TTS) 생성 중... [{req.provider}]")
        tts_path, tts_duration, words = generate_tts(
            script["narration"],
            provider=req.provider,
            voice_id=req.voice_id,
        )
        video_duration = min(round(tts_duration) + 2, req.duration)

        upd(62, "배경 이미지 생성 중...")
        bg_path = create_background_frame(
            script["hook"],
            pill_text=req.pill,
            clipless=(clip_path is None),
            hook_accent_color=req.hook_accent_color,
            bg_template=req.bg_template,
        )

        upd(72, "자막 생성 중...")
        raw_subs = script.get("subtitles") or []
        chunks   = [s["text"] if isinstance(s, dict) else str(s) for s in raw_subs]
        if not chunks:
            chunks = chunk_narration(script["narration"])
        ass_path = generate_chunk_ass(
            chunks, words, tts_duration,
            highlight_color=req.subtitle_color,
        )

        upd(78, "GIF 페치 중...")
        gif_records = _fetch_gifs(script.get("gifs") or [], video_duration=video_duration)

        upd(85, "영상 합성 중...")
        bgm_path = config.BGM_MAP.get(req.bgm, config.BGM_FALLBACK)
        if not Path(str(bgm_path)).exists():
            bgm_path = config.BGM_FALLBACK

        keyword = re.sub(r'[\\/:*?"<>|]', "", script["hook"][:20]).replace(" ", "_")
        output_path = config.OUTPUT_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{keyword}.mp4"

        provider_key = (req.provider or "elevenlabs").lower()
        voice_gain   = config.TTS_VOICE_GAIN.get(provider_key, config.TTS_VOICE_GAIN["elevenlabs"])
        compose_video(clip_path, bg_path, ass_path, output_path, bgm_path,
                      tts_path=tts_path, duration=video_duration, gifs=gif_records,
                      voice_gain=voice_gain)

        jobs[job_id].update({
            "status": "done", "progress": 100,
            "message": "완료!", "output": output_path.name, "script": script,
        })

    except Exception as e:
        jobs[job_id].update({"status": "error", "progress": 0, "message": str(e)})


def _run_youtube_upload(
    job_id: str,
    file_path: Path,
    req: YouTubeUploadRequest,
    *,
    record_id: Optional[str] = None,
) -> None:
    """업로드 백그라운드 워커. job(SSE) + store(영속) 양쪽 동시 갱신 + Telegram 알림."""
    from pipeline.youtube_publisher import upload_video
    from pipeline import upload_store, notifier

    def cb(p: float):
        pct = int(min(100, max(0, p * 100)))
        jobs[job_id].update({
            "status":   "running",
            "progress": pct,
            "message":  f"YouTube 전송 중... {pct}%",
        })
        if record_id:
            upload_store.update_progress(record_id, pct)

    try:
        cb(0.0)
        result = upload_video(
            file_path,
            title=req.title,
            description=req.description,
            tags=req.tags,
            category_id=req.category_id,
            privacy_status=req.privacy_status,
            made_for_kids=req.made_for_kids,
            progress_cb=cb,
        )
        jobs[job_id].update({
            "status":    "done", "progress": 100,
            "message":   "업로드 완료!",
            "video_id":  result["video_id"],
            "video_url": result["url"],
        })
        if record_id:
            updated = upload_store.mark_done(
                record_id,
                video_id=result["video_id"],
                video_url=result["url"],
            )
            if updated and notifier.is_enabled():
                ok = notifier.notify_upload_success(updated)
                if ok:
                    upload_store.update(record_id, telegram_notified=True)
    except Exception as e:
        jobs[job_id].update({"status": "error", "progress": 0, "message": str(e)})
        if record_id:
            updated = upload_store.mark_failed(record_id, error=str(e))
            if updated and notifier.is_enabled():
                ok = notifier.notify_upload_failed(updated)
                if ok:
                    upload_store.update(record_id, telegram_notified=True)


_FALLBACK_GIF_KEYWORDS = ["mind blown", "wow", "shocked", "amazing", "no way"]


def _coerce_float(value, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _fetch_gifs(specs: list, video_duration: float = 55.0, *, fallback: bool = True) -> list[dict]:
    """script['gifs'] 항목들을 Klipy로 페치. Claude가 빠뜨리거나 모두 실패하면
    안전망으로 기본 키워드 GIF 1개를 영상 1/3 지점에 삽입."""
    from pipeline.gif_fetch import fetch as fetch_gif

    out: list[dict] = []
    for g in (specs or []):
        kw = g.get("keyword_en") or g.get("keyword")
        if not kw:
            continue
        try:
            path = fetch_gif(kw)
            out.append({
                "path":     path,
                "start":    max(0.0, _coerce_float(g.get("start"), 0.0)),
                "duration": max(0.5, _coerce_float(g.get("duration"), 2.0)),
                "size":     max(120, _coerce_int(g.get("size"), 600)),
            })
        except Exception as exc:
            print(f"[gif] {kw!r} fetch 실패: {exc}", flush=True)

    # Fallback — 결과가 비어 있으면 영상 임팩트 보장 위해 기본 GIF 1개
    if fallback and not out:
        for kw in _FALLBACK_GIF_KEYWORDS:
            try:
                path = fetch_gif(kw)
                out.append({
                    "path":     path,
                    "start":    max(2.0, video_duration / 3),
                    "duration": 2.0,
                    "size":     600,
                })
                print(f"[gif] fallback {kw!r} 사용", flush=True)
                break
            except Exception as exc:
                print(f"[gif] fallback {kw!r} 실패: {exc}", flush=True)
    return out


if __name__ == "__main__":
    import uvicorn, os
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=True)
