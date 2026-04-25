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


class RenderRequest(BaseModel):
    articles: list[str] = []
    urls: list[str] = []
    start_times: list[str] = ["00:00:00", "00:00:00"]
    duration: int = 55
    bgm: str = "bgm_light"
    voice_id: str = config.ELEVENLABS_VOICE_ID
    script: Optional[dict] = None   # pre-generated or manually edited


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "default_voice_id": config.ELEVENLABS_VOICE_ID,
            "bgm_options": list(config.BGM_MAP.keys()),
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
        if not url:
            raise ValueError("유튜브 URL을 입력해주세요")

        upd(10, "영상 다운로드 중...")
        clip_path = download_clip(url, req.start_times[0] or "00:00:00", req.duration)

        upd(25, "스크립트 생성 중...")
        if req.script:
            script = req.script
        else:
            articles = [a.strip() for a in req.articles if a.strip()]
            if not articles:
                raise ValueError("기사를 입력해주세요")
            script = generate_script_from_articles(articles)

        upd(45, "음성(TTS) 생성 중...")
        tts_path, tts_duration, words = generate_tts(script["narration"], voice_id=req.voice_id)
        video_duration = min(round(tts_duration) + 2, req.duration)

        upd(62, "배경 이미지 생성 중...")
        bg_path = create_background_frame(script["hook"], script["hashtags"])

        upd(72, "자막 생성 중...")
        raw_subs = script.get("subtitles") or []
        chunks   = [s["text"] if isinstance(s, dict) else str(s) for s in raw_subs]
        if not chunks:
            chunks = chunk_narration(script["narration"])
        ass_path = generate_chunk_ass(chunks, words, tts_duration)

        upd(82, "영상 합성 중...")
        bgm_path = config.BGM_MAP.get(req.bgm, config.BGM_FALLBACK)
        if not Path(str(bgm_path)).exists():
            bgm_path = config.BGM_FALLBACK

        keyword = re.sub(r'[\\/:*?"<>|]', "", script["hook"][:20]).replace(" ", "_")
        output_path = config.OUTPUT_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{keyword}.mp4"

        compose_video(clip_path, bg_path, ass_path, output_path, bgm_path,
                      tts_path=tts_path, duration=video_duration)

        jobs[job_id].update({
            "status": "done", "progress": 100,
            "message": "완료!", "output": output_path.name, "script": script,
        })

    except Exception as e:
        jobs[job_id].update({"status": "error", "progress": 0, "message": str(e)})


if __name__ == "__main__":
    import uvicorn, os
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=True)
