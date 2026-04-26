import sys
import uuid
import json
import threading
import asyncio
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import config

app = FastAPI(title="힙포인사이트 쇼츠 생성기")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

jobs: dict[str, dict] = {}


# ── Request models ────────────────────────────────────────────────────────────

class ScriptRequest(BaseModel):
    articles: list[str] = []
    title: Optional[str] = None


class FactCheckRequest(BaseModel):
    articles: list[str] = []
    script:   str = ""


class TTSPreviewRequest(BaseModel):
    provider: str = "typecast"       # elevenlabs | typecast
    voice_id: Optional[str] = None
    text:     str = "안녕하세요, 힙포인사이트입니다. 오늘도 흥미로운 AI 소식을 들고 왔어요."


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


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "default_voice_id":          config.TYPECAST_VOICE_ID,
            "default_typecast_voice_id": config.TYPECAST_VOICE_ID,
            "bgm_options":               [k for k, p in config.BGM_MAP.items() if Path(str(p)).exists()],
        },
    )


@app.post("/api/generate-script")
async def generate_script_api(req: ScriptRequest):
    from pipeline.script_generator import generate_script_from_articles, generate_script
    try:
        articles = [a.strip() for a in req.articles if a.strip()]
        if articles:
            script = generate_script_from_articles(articles)
        elif req.title:
            script = generate_script(req.title)
        else:
            return {"error": "기사 또는 제목을 입력해주세요"}
        return {"script": script}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/fact-check")
async def fact_check_api(req: FactCheckRequest):
    from pipeline.script_generator import fact_check
    articles = [a.strip() for a in req.articles if a.strip()]
    if not articles:
        return {"error": "팩트체크하려면 원본 기사가 필요해요"}
    if not req.script.strip():
        return {"error": "검증할 스크립트가 비어있어요"}
    try:
        result = fact_check(articles, req.script)
        return {"result": result}
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
    file_path = config.OUTPUT_DIR / req.filename
    if not file_path.exists():
        return {"error": f"파일을 찾을 수 없어요: {req.filename}"}
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "status": "queued", "progress": 0,
        "message": "YouTube 업로드 준비 중...", "output": None, "error": None,
    }
    threading.Thread(target=_run_youtube_upload, args=(job_id, file_path, req), daemon=True).start()
    return {"job_id": job_id}


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


@app.post("/api/preview-clip")
async def preview_clip_api(req: PreviewClipRequest):
    from pipeline.multiclip import parse_time, prepare_preview
    try:
        start = parse_time(req.start)
        end   = parse_time(req.end)
        if end <= start:
            return {"error": "종료 시간이 시작보다 커야 해요"}
        if end - start > 60:
            return {"error": "한 컷 최대 60초까지"}
        info = prepare_preview(req.url, start, end)
        return {
            "clip_id":   info["clip_id"],
            "duration":  info["duration"],
            "video_url": f"/api/preview-asset/{info['clip_id']}/video",
            "thumb_url": f"/api/preview-asset/{info['clip_id']}/thumb",
        }
    except Exception as e:
        return {"error": f"미리보기 실패: {e}"}


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
    if not (2 <= len(req.clips) <= 5):
        return {"error": "클립은 2~5개"}
    if len(req.transitions) != len(req.clips) - 1:
        return {"error": f"transitions 개수가 {len(req.clips)-1}개여야 해요"}
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
        from pipeline.tts import generate_tts
        from pipeline.subtitle import generate_chunk_ass, chunk_narration
        from pipeline.editor import create_background_frame, compose_video

        config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # 1) 스크립트 확인 + TTS 생성
        upd(8, "스크립트 확인 중...")
        script = dict(req.script or {})
        narration = script.get("narration") or []
        if isinstance(narration, str):
            narration = [line.strip() for line in narration.splitlines() if line.strip()]
        else:
            narration = [str(line).strip() for line in narration if str(line).strip()]
        if not narration:
            raise ValueError("나레이션을 먼저 생성하거나 입력해주세요")

        hook_text = (req.hook or script.get("hook") or "힙포인사이트").strip()
        script["hook"] = hook_text
        script["narration"] = narration

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
            hook_text, pill_text=req.pill, clipless=False,
        )

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
        chunks = [s["text"] if isinstance(s, dict) else str(s) for s in raw_subs]
        if not chunks:
            chunks = chunk_narration(narration)
        ass_path = generate_chunk_ass(chunks, words, tts_duration)

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
        jobs[job_id].update({
            "status": "error", "progress": 100,
            "message": str(e), "error": str(e),
        })


@app.get("/api/template-preview")
async def template_preview(pill: str = "", hook: str = ""):
    from pipeline.editor import create_template_preview
    hook_text = hook.strip() or "훅 텍스트|강조 한 줄"
    pill_text = pill.strip()
    path = create_template_preview(hook_text, pill_text=pill_text)
    return FileResponse(
        str(path),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/template-preview-window", response_class=HTMLResponse)
async def template_preview_window(pill: str = "", hook: str = "", t: str = ""):
    """모바일 폰 크기 새 창으로 띄우는 PNG 뷰어 페이지."""
    from urllib.parse import urlencode
    qs = urlencode({"pill": pill, "hook": hook, "t": t}, encoding="utf-8")
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
        from pipeline.script_generator import generate_script_from_articles
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
            script = generate_script_from_articles(articles)

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
        )

        upd(72, "자막 생성 중...")
        raw_subs = script.get("subtitles") or []
        chunks   = [s["text"] if isinstance(s, dict) else str(s) for s in raw_subs]
        if not chunks:
            chunks = chunk_narration(script["narration"])
        ass_path = generate_chunk_ass(chunks, words, tts_duration)

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


def _run_youtube_upload(job_id: str, file_path: Path, req: YouTubeUploadRequest) -> None:
    from pipeline.youtube_publisher import upload_video

    def cb(p: float):
        jobs[job_id].update({
            "status":   "running",
            "progress": int(min(100, max(0, p * 100))),
            "message":  f"YouTube 전송 중... {int(p*100)}%",
        })

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
    except Exception as e:
        jobs[job_id].update({"status": "error", "progress": 0, "message": str(e)})


_FALLBACK_GIF_KEYWORDS = ["mind blown", "wow", "shocked", "amazing", "no way"]


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
                "start":    float(g.get("start", 0)),
                "duration": float(g.get("duration", 2.0)),
                "size":     int(g.get("size", 600)),
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
