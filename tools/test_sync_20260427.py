"""Render a 45-second sync test from the 20260427 source video.

This intentionally generates fresh TTS from the narration below, then builds
subtitles from the same text so audio and captions share one timing source.
"""
from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
from pipeline.editor import compose_video, create_background_frame
from pipeline.subtitle import chunk_narration, generate_chunk_ass
from pipeline.tts import generate_tts


SOURCE_VIDEO = config.TEMP_DIR / "443a0724_montage.mp4"
SOURCE_FALLBACK = config.OUTPUT_DIR / "20260427_000412_로봇_마라톤__무서운_중국.mp4"
IAN_VOICE_ID = "tc_62d66c3ef075c6ebd4114bd5"
OUTPUT_VIDEO = config.OUTPUT_DIR / "20260427_ian_sample_no_pill.mp4"
FRAME_CHECK = config.TEMP_DIR / "20260427_ian_sample_no_pill_frame.png"

NARRATION = [
    "중국의 한 로봇이 마라톤 코스에 등장했습니다.",
    "사람처럼 팔을 흔들고, 옆 선수들과 같은 방향으로 달렸죠.",
    "완주 기록은 아주 빠르진 않았지만, 장면 자체는 꽤 강렬했습니다.",
    "중요한 건 속도가 아니라, 로봇이 긴 코스를 멈추지 않고 버텼다는 점입니다.",
    "배터리와 관절 제어, 균형 보정이 동시에 맞아야 가능한 일이거든요.",
    "예전엔 연구실 안에서만 보이던 동작이 이제는 실제 도로 위로 나오고 있습니다.",
    "물론 아직 인간 선수를 대체할 단계는 아닙니다.",
    "하지만 오늘의 느린 완주가 내일의 배송, 구조, 경비 로봇으로 이어질 수 있죠.",
    "특히 사람 많은 코스에서 길을 유지하고, 속도를 조절했다는 점이 눈에 띕니다.",
    "지금은 이벤트처럼 보이지만, 이런 데이터가 쌓이면 움직임은 더 빨리 자연스러워질 겁니다.",
    "그래서 이 장면은 웃긴 뉴스이자, 꽤 현실적인 신호입니다.",
    "여러분은 로봇 마라톤을 신기한 이벤트로 보시나요, 아니면 미래의 예고편으로 보시나요?",
]


def _duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def main() -> None:
    source_video = SOURCE_VIDEO if SOURCE_VIDEO.exists() else SOURCE_FALLBACK
    if not source_video.exists():
        raise SystemExit(f"source video not found: {source_video}")

    config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    hook_text = "로봇 마라톤 | 미래가 뛰기 시작했다"
    bg_path = create_background_frame(hook_text=hook_text, pill_text="", clipless=False)

    tts_path, tts_duration, words = generate_tts(
        NARRATION,
        provider="typecast",
        voice_id=IAN_VOICE_ID,
    )
    chunks = chunk_narration(NARRATION, min_chars=4, max_chars=10)
    ass_path = generate_chunk_ass(chunks, words, tts_duration)

    target_duration = 45
    if tts_duration > target_duration:
        target_duration = int(math.ceil(tts_duration)) + 1

    compose_video(
        clip_path=source_video,
        bg_path=bg_path,
        ass_path=ass_path,
        output_path=OUTPUT_VIDEO,
        bgm_path=config.BGM_MAP.get("bgm_future", config.BGM_FALLBACK),
        tts_path=tts_path,
        duration=target_duration,
        voice_gain=config.TTS_VOICE_GAIN.get("typecast", 2.0),
        bgm_volume=config.BGM_VOLUME,
    )

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            "10",
            "-i",
            str(OUTPUT_VIDEO),
            "-frames:v",
            "1",
            str(FRAME_CHECK),
        ],
        check=True,
    )

    print(f"OUTPUT={OUTPUT_VIDEO}")
    print(f"OUTPUT_DURATION={_duration(OUTPUT_VIDEO):.3f}")
    print(f"TTS_DURATION={tts_duration:.3f}")
    print(f"WORDS={len(words)}")
    print(f"SUBTITLES={len(chunks)}")
    print(f"FRAME={FRAME_CHECK}")


if __name__ == "__main__":
    main()
