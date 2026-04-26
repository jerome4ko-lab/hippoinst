"""캐릭터 정렬 검증용 1회용 렌더 스크립트.

캐시된 미리보기 클립 3개 + 기존 temp/tts.mp3 를 그대로 활용해
- 새 CHARACTER_SIZE (270)
- 새 overlay 좌표 (W-w-100, CLIP_Y+CLIP_H-cs)
가 적용된 최종 영상을 한 번 합성한다.

API를 거치지 않고 pipeline 함수를 직접 호출 → 새 클립 다운로드/TTS 재생성 비용 0.
"""
from __future__ import annotations
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
from pipeline.tts import _fake_words_from_text
from pipeline.subtitle import generate_chunk_ass, chunk_narration
from pipeline.editor import create_background_frame, compose_video
from pipeline.multiclip import compose_montage


narration = [
    "로봇이 마라톤에서 인간을 앞질렀어요.",
    "중국 스마트폰 회사 Honor의 로봇이에요.",
    "이름은 2026 베이징 반마라톤에 출전했는데요.",
    "기록이 무려 50분 26초였어요.",
    "그런데 이게 왜 충격적이냐면요.",
    "7개월 전 인간 세계기록보다 무려 7분이나 빠른 기록이에요.",
    "반마라톤은 21킬로미터예요.",
]
text = "\n".join(narration)
hook_text = "캐릭터 정렬 테스트|클립 하단에 정확히"
pill_text = "#TEST"

tts_path = config.TEMP_DIR / "tts.mp3"
if not tts_path.exists():
    raise SystemExit(f"기존 TTS 파일이 없어요: {tts_path}")

dur_proc = subprocess.run(
    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
     "-of", "default=nw=1:nk=1", str(tts_path)],
    capture_output=True, text=True,
)
tts_duration = float(dur_proc.stdout.strip())
words = _fake_words_from_text(text, tts_duration, audio_path=tts_path)

clips = [
    {"path": config.TEMP_DIR / "preview" / "f343c888d608.mp4", "duration": 17.0},
    {"path": config.TEMP_DIR / "preview" / "7a9f4bfd20b6.mp4", "duration": 14.0},
    {"path": config.TEMP_DIR / "preview" / "0ea8f3f255ba.mp4", "duration": 10.0},
]
for c in clips:
    if not c["path"].exists():
        raise SystemExit(f"캐시 클립 없음: {c['path']}")

bg_path = create_background_frame(hook_text=hook_text, pill_text=pill_text, clipless=False)

chunks = chunk_narration(narration)
ass_path = generate_chunk_ass(chunks, words, tts_duration)

montage_path = config.TEMP_DIR / "test_character_montage.mp4"
compose_montage(clips, transitions=["fade", "wipeleft"], output_path=montage_path)

output_path = config.OUTPUT_DIR / "20260427_test_character.mp4"
compose_video(
    bg_path=bg_path,
    clip_path=montage_path,
    ass_path=ass_path,
    tts_path=tts_path,
    bgm_path=config.BGM_FALLBACK,
    output_path=output_path,
    duration=int(math.ceil(tts_duration)) + 2,
    voice_gain=config.TTS_VOICE_GAIN["typecast"],
    bgm_volume=config.BGM_VOLUME,
)

# 캐릭터 보이는 5초 시점 한 프레임 추출
frame_out = config.TEMP_DIR / "_test_character_frame.png"
subprocess.run(
    ["ffmpeg", "-y", "-loglevel", "error", "-ss", "5",
     "-i", str(output_path), "-frames:v", "1", str(frame_out)],
    check=True,
)
print(f"OUTPUT: {output_path}")
print(f"FRAME:  {frame_out}")
print(f"SIZE:   {output_path.stat().st_size}")
