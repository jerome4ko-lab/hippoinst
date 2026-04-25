"""Python 파이프라인 → Remotion 렌더 오케스트레이터.

- 영상 다운로드, 스크립트 생성, TTS, 자막 청크 정렬, Klipy GIF 페치까지 Python에서 수행
- 모든 자산을 remotion/public/job_{ts}/ 로 복사
- props.json 작성 후 `npx remotion render HippoShort` 호출
- 결과 mp4를 output/{date}_{slug}.mp4 로 복사
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Windows 콘솔(cp949) 호환 — 유니코드 출력이 가능하면 그대로, 아니면 무시
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import config
from pipeline.downloader        import download_clip
from pipeline.script_generator  import (
    generate_script,
    generate_script_from_articles,
    load_articles,
)
from pipeline.tts               import generate_tts
from pipeline.subtitle          import align_chunks_to_words, chunk_narration
from pipeline.gif_fetch         import fetch as fetch_gif

_REMOTION_DIR = config.BASE_DIR / "remotion"
_REMOTION_PUBLIC = _REMOTION_DIR / "public"
_ARTICLES_FILE   = config.BASE_DIR / "articles.txt"


def render_short(
    url: str,
    start: str = "00:00:00",
    duration: int = config.VIDEO_DURATION,
    title: str | None = None,
    bgm_override: str | None = None,
    gif_mode: bool = True,
) -> Path:
    config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[1/7] 영상 다운로드 ({duration}s) …")
    clip_path = download_clip(url, start, duration)

    print("[2/7] 스크립트 생성 …")
    script = _make_script(title)
    print(f"      훅: {script['hook']}  |  BGM: {script['bgm_tag']}")
    print(f"      narration {len(script['narration'])}줄, "
          f"subtitles {len(script.get('subtitles') or [])}청크, "
          f"gifs {len(script.get('gifs') or [])}개")

    print("[3/7] 음성(TTS) 생성 …")
    tts_path, tts_duration, words = generate_tts(script["narration"])
    print(f"      → {tts_path.name} ({tts_duration:.1f}s, {len(words)}단어)")
    video_duration = min(round(tts_duration) + 2, duration)

    print("[4/7] 자막 청크 정렬 …")
    raw_subs = script.get("subtitles") or []
    chunks = [s["text"] if isinstance(s, dict) else str(s) for s in raw_subs]
    if not chunks:
        chunks = chunk_narration(script["narration"])
    aligned = align_chunks_to_words(chunks, words)
    print(f"      → {len(aligned)}청크 정렬 완료")

    gif_records: list[dict] = []
    if gif_mode and script.get("gifs"):
        print(f"[5/7] Klipy GIF 페치 ({len(script['gifs'])}개) …")
        for g in script["gifs"]:
            kw = g.get("keyword_en") or g.get("keyword")
            if not kw:
                continue
            try:
                local = fetch_gif(kw)
                gif_records.append({
                    "src":      local,
                    "start":    float(g["start"]),
                    "duration": float(g.get("duration", 2.0)),
                    "keyword":  kw,
                })
                print(f"      ✓ {kw!r} → {local.name}")
            except Exception as exc:
                print(f"      ✗ {kw!r} 실패: {exc}", file=sys.stderr)
    else:
        print("[5/7] GIF 모드 OFF — 건너뜀")

    print("[6/7] Remotion 자산 디렉터리 구성 …")
    job_id  = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_dir = _REMOTION_PUBLIC / f"job_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)

    bgm_tag  = bgm_override or script.get("bgm_tag") or "bgm_light"
    bgm_src  = config.BGM_MAP.get(bgm_tag, config.BGM_FALLBACK)
    if not Path(bgm_src).exists():
        bgm_src = config.BGM_FALLBACK

    asset_clip = _stage(clip_path, job_dir / "clip.mp4")
    asset_tts  = _stage(tts_path,  job_dir / "tts.mp3")
    asset_bgm  = _stage(Path(bgm_src), job_dir / "bgm.mp3") if Path(bgm_src).exists() else None

    gif_props = []
    for i, g in enumerate(gif_records):
        ext = g["src"].suffix
        dst = job_dir / f"gif_{i}{ext}"
        _stage(g["src"], dst)
        gif_props.append({
            "src":      f"job_{job_id}/{dst.name}",
            "start":    g["start"],
            "duration": g["duration"],
            "size":     600,
            "rotate":   -6 if i % 2 == 0 else 5,
        })

    rel = lambda p: f"job_{job_id}/{p.name}" if p else None
    props = {
        "hook":              script["hook"],
        "hashtags":          script["hashtags"],
        "bgImageSrc":        None,
        "clipSrc":           rel(asset_clip),
        "ttsSrc":            rel(asset_tts),
        "bgmSrc":            rel(asset_bgm),
        "bgmVolume":         0.08,
        "ttsVolume":         1.0,
        "durationInSeconds": float(video_duration),
        "subtitles":         aligned,
        "gifs":              gif_props,
    }
    props_path = job_dir / "props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"      → {props_path.relative_to(config.BASE_DIR)}")

    print("[7/7] Remotion 렌더 …")
    out_name = _format_output_name(script["hook"], title)
    output_path = config.OUTPUT_DIR / out_name
    npx = "npx.cmd" if os.name == "nt" else "npx"
    cmd = [
        npx, "remotion", "render", "HippoShort",
        str(output_path),
        f"--props=public/job_{job_id}/props.json",
    ]
    subprocess.run(cmd, cwd=_REMOTION_DIR, check=True)
    print(f"\n완료 → {output_path}")
    return output_path


def _make_script(title: str | None) -> dict:
    if _ARTICLES_FILE.exists():
        articles = load_articles(_ARTICLES_FILE)
        if articles:
            return generate_script_from_articles(articles)
    if not title:
        sys.exit("articles.txt가 없으면 --title 이 필요합니다.")
    return generate_script(title)


def _stage(src: Path, dst: Path) -> Path:
    if dst.exists() and dst.stat().st_size == src.stat().st_size:
        return dst
    shutil.copy2(src, dst)
    return dst


def _format_output_name(hook: str, title: str | None) -> str:
    raw = (title or hook)[:24]
    keyword = re.sub(r'[\\/:*?"<>|]', "", raw).strip().replace(" ", "_") or "short"
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{date_str}_{keyword}_remotion.mp4"


def _cli():
    p = argparse.ArgumentParser(description="힙포인사이트 Remotion 렌더 파이프라인")
    p.add_argument("--url",      required=True,            help="유튜브 URL")
    p.add_argument("--start",    default="00:00:00",       help="클립 시작 시간 HH:MM:SS")
    p.add_argument("--duration", default=config.VIDEO_DURATION, type=int, help="클립 길이 (초)")
    p.add_argument("--title",    default=None,             help="articles.txt 없을 때 주제")
    p.add_argument("--bgm",      default=None,             help="bgm_tag 강제 지정")
    p.add_argument("--no-gifs",  action="store_true",      help="GIF 오버레이 비활성")
    args = p.parse_args()

    render_short(
        url          = args.url,
        start        = args.start,
        duration     = args.duration,
        title        = args.title,
        bgm_override = args.bgm,
        gif_mode     = not args.no_gifs,
    )


if __name__ == "__main__":
    _cli()
