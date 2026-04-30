import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import config
from pipeline.downloader       import download_clip
from pipeline.script_generator import (
    generate_script,
    generate_script_from_articles,
    load_articles,
    normalize_script_shape,
)
from pipeline.subtitle         import generate_chunk_ass, chunk_narration
from pipeline.editor           import create_background_frame, compose_video
from pipeline.tts              import generate_tts  # returns (path, duration, words)

_ARTICLES_FILE = config.BASE_DIR / "articles.txt"


def main():
    parser = argparse.ArgumentParser(description="힙포인사이트 쇼츠 자동 생성기")
    parser.add_argument("--url",      required=True,        help="유튜브 URL")
    parser.add_argument("--title",    default=None,         help="영상 제목/주제 (articles.txt 없을 때 필수)")
    parser.add_argument("--start",    default="00:00:00",   help="클립 시작 시간 (HH:MM:SS)")
    parser.add_argument("--duration", type=int, default=55, help="클립 길이 (초)")
    args = parser.parse_args()

    config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[1/6] 영상 다운로드 중...")
    clip_path = download_clip(args.url, args.start, args.duration)
    print(f"      → {clip_path}")

    if _ARTICLES_FILE.exists():
        articles = load_articles(_ARTICLES_FILE)
        if articles:
            print(f"[2/6] 스크립트 생성 중: articles.txt ({len(articles)}개 기사 기반)")
            script = generate_script_from_articles(articles)["script"]
        else:
            script = _script_from_title(args)
    else:
        script = _script_from_title(args)
    script = normalize_script_shape(script)

    hook_text = script["hook"]
    print(f"      훅:      {hook_text}")
    print(f"      BGM 태그: {script['bgm_tag']}")
    print(f"      해시태그: {script['hashtags']}")
    script_path = config.OUTPUT_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_script.txt"
    _save_script(script, script_path)
    print(f"      스크립트 → {script_path}")

    print("[3/6] 음성(TTS) 생성 중...")
    tts_path, tts_duration, words = generate_tts(script["narration"])
    print(f"      → {tts_path} ({tts_duration:.1f}초, 단어 {len(words)}개)")
    video_duration = min(round(tts_duration) + 2, args.duration)

    print("[4/6] 배경 이미지 생성 중...")
    bg_path = create_background_frame(hook_text, pill_text=script["hashtags"])
    print(f"      → {bg_path}")

    print("[5/6] 자막(ASS) 생성 중...")
    raw_subs = script.get("subtitles") or []
    chunks   = [s["text"] if isinstance(s, dict) else str(s) for s in raw_subs]
    if not chunks:
        chunks = chunk_narration(script["narration"])
    ass_path = generate_chunk_ass(chunks, words, tts_duration)
    print(f"      → {ass_path} (청크 {len(chunks)}개)")

    print("[6/6] 영상 합성 중...")
    bgm_path = config.BGM_MAP.get(script["bgm_tag"], config.BGM_FALLBACK)
    if not Path(bgm_path).exists():
        print(f"      ※ {script['bgm_tag']} 파일 없음 → bgm_light로 대체")
        bgm_path = config.BGM_FALLBACK

    raw_keyword = (args.title or hook_text)[:20]
    keyword     = re.sub(r'[\\/:*?"<>|]', "", raw_keyword).replace(" ", "_")
    date_str    = datetime.now().strftime("%Y%m%d")
    output_path = config.OUTPUT_DIR / f"{date_str}_{keyword}.mp4"

    compose_video(clip_path, bg_path, ass_path, output_path, bgm_path,
                  tts_path=tts_path, duration=video_duration)

    print(f"\n완료! → {output_path}")


def _save_script(script: dict, path: Path) -> None:
    hook_text = script.get("hook", "")
    hashtags = script.get("hashtags", "")
    lines = [
        f"훅:      {hook_text}",
        f"BGM 태그: {script['bgm_tag']}",
        f"해시태그: {hashtags}",
        "",
        "── 나레이션 ───────────────────────────",
    ]
    for i, line in enumerate(script["narration"], 1):
        lines.append(f"  {i:>2}. {line}")
    path.write_text("\n".join(lines), encoding="utf-8")


def _script_from_title(args):
    if not args.title:
        print("오류: articles.txt가 없으면 --title 이 필요합니다.", file=sys.stderr)
        sys.exit(1)
    print(f"[2/5] 스크립트 생성 중: '{args.title}'")
    return generate_script(args.title)


if __name__ == "__main__":
    main()
