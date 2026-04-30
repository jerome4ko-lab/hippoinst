"""캐릭터 정렬 검증용 1회용 렌더 스크립트.

캐시된 미리보기 클립 3개 + 기존 temp/tts.mp3 를 그대로 활용해
- config.CHARACTER_SIZE
- 새 overlay 좌표 (W-w-CHARACTER_RIGHT_INSET, SUB_Y+SUB_H+gap)
가 적용된 최종 영상을 한 번 합성한다.

API를 거치지 않고 pipeline 함수를 직접 호출 → 새 클립 다운로드/TTS 재생성 비용 0.
"""
from __future__ import annotations
import argparse
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
from PIL import Image, ImageDraw, ImageFont
from pipeline.tts import _fake_words_from_text
from pipeline.subtitle import generate_chunk_ass, chunk_narration
from pipeline.editor import (
    CHARACTER_BELOW_SUBTITLE_GAP,
    create_background_frame,
    compose_video,
)
from pipeline.multiclip import compose_montage


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render character placement test video.")
    parser.add_argument(
        "--guide",
        action="store_true",
        help="draw coordinate grid, subtitle box, and character box on the extracted frame",
    )
    return parser.parse_args()


def _character_geometry() -> dict[str, int]:
    size = int(config.CHARACTER_SIZE)
    right_inset = int(getattr(config, "CHARACTER_RIGHT_INSET", 90))
    subtitle_bottom = int(config.SUB_Y + config.SUB_H)
    below_subtitle_gap = int(
        getattr(config, "CHARACTER_BELOW_SUBTITLE_GAP", CHARACTER_BELOW_SUBTITLE_GAP)
    )
    x = int(config.VIDEO_WIDTH - size - right_inset)
    y = min(subtitle_bottom + below_subtitle_gap, int(config.VIDEO_HEIGHT - size))
    return {
        "x": x,
        "y": y,
        "size": size,
        "right_inset": right_inset,
        "below_subtitle_gap": below_subtitle_gap,
        "subtitle_bottom": subtitle_bottom,
    }


def _print_geometry(geometry: dict[str, int]) -> None:
    print(f"current: x={geometry['x']}, y={geometry['y']}, size={geometry['size']}")
    print(
        "right_inset="
        f"{geometry['right_inset']}, "
        f"below_subtitle_gap={geometry['below_subtitle_gap']}"
    )
    print(
        "formula: right_inset = "
        f"{config.VIDEO_WIDTH} - {geometry['size']} - target_x"
    )
    print(f"formula: below_subtitle_gap = target_y - {geometry['subtitle_bottom']}")
    print(
        "example: target x=761 -> CHARACTER_RIGHT_INSET="
        f"{config.VIDEO_WIDTH - geometry['size'] - 761}"
    )
    print(
        "example: target y=1564 -> CHARACTER_BELOW_SUBTITLE_GAP="
        f"{1564 - geometry['subtitle_bottom']}"
    )


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(config.FONT_BOLD, size)
    except OSError:
        return ImageFont.load_default()


def _draw_label(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    *,
    fill: tuple[int, int, int, int] = (255, 255, 255, 255),
    bg: tuple[int, int, int, int] = (0, 0, 0, 180),
) -> None:
    font = _load_font(24)
    x, y = xy
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 8, 5
    draw.rectangle((x, y, x + w + pad_x * 2, y + h + pad_y * 2), fill=bg)
    draw.text((x + pad_x - bbox[0], y + pad_y - bbox[1]), text, font=font, fill=fill)


def _draw_coordinate_guide(frame_path: Path, geometry: dict[str, int]) -> Path:
    img = Image.open(frame_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width, height = img.size

    thin = (255, 255, 255, 50)
    thick = (255, 215, 0, 105)
    axis = (0, 255, 200, 150)
    subtitle = (0, 170, 255, 130)
    character = (255, 80, 80, 190)

    for x in range(0, width + 1, 50):
        color = thick if x % 100 == 0 else thin
        line_w = 2 if x % 100 == 0 else 1
        draw.line((x, 0, x, height), fill=color, width=line_w)
        if x % 100 == 0 and x < width:
            _draw_label(draw, (x + 4, 4), str(x), fill=(255, 215, 0, 255))

    for y in range(0, height + 1, 50):
        color = thick if y % 100 == 0 else thin
        line_w = 2 if y % 100 == 0 else 1
        draw.line((0, y, width, y), fill=color, width=line_w)
        if y % 100 == 0 and y < height:
            _draw_label(draw, (4, y + 4), str(y), fill=(255, 215, 0, 255))

    draw.line((0, 0, width, 0), fill=axis, width=3)
    draw.line((0, 0, 0, height), fill=axis, width=3)

    sub_top = int(config.SUB_Y)
    sub_bottom = int(config.SUB_Y + config.SUB_H)
    draw.rectangle((0, sub_top, width - 1, sub_bottom), outline=subtitle, width=5)
    _draw_label(
        draw,
        (18, sub_top + 12),
        f"subtitle y={sub_top}..{sub_bottom}",
        fill=(0, 220, 255, 255),
    )

    x = geometry["x"]
    y = geometry["y"]
    size = geometry["size"]
    draw.rectangle((x, y, x + size, y + size), outline=character, width=6)
    draw.line((x, y, x + 34, y), fill=character, width=10)
    draw.line((x, y, x, y + 34), fill=character, width=10)
    _draw_label(
        draw,
        (max(8, min(x, width - 420)), max(8, y - 46)),
        f"char x={x} y={y} {size}x{size}",
        fill=(255, 120, 120, 255),
    )

    guided = Image.alpha_composite(img, overlay).convert("RGB")
    guide_path = frame_path.with_name(frame_path.stem + "_guide.png")
    guided.save(guide_path)
    return guide_path


args = _parse_args()
geometry = _character_geometry()

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
hook_text = "캐릭터 정렬 테스트|자막 아래에 정확히"
pill_text = ""

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

output_path = (
    config.OUTPUT_DIR
    / f"20260428_test_character_x{geometry['x']}_y{geometry['y']}.mp4"
)
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
frame_out = config.TEMP_DIR / f"_test_character_x{geometry['x']}_y{geometry['y']}_frame.png"
subprocess.run(
    ["ffmpeg", "-y", "-loglevel", "error", "-ss", "5",
     "-i", str(output_path), "-frames:v", "1", str(frame_out)],
    check=True,
)
guide_path = _draw_coordinate_guide(frame_out, geometry) if args.guide else None

_print_geometry(geometry)
print(f"OUTPUT: {output_path}")
print(f"FRAME:  {frame_out}")
if guide_path is not None:
    print(f"GUIDE:  {guide_path}")
print(f"SIZE:   {output_path.stat().st_size}")
