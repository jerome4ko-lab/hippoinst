import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import config


def create_background_frame(hook_text: str, hashtag_text: str) -> Path:
    """Render static background (banner + hook title + hashtag) as PNG."""
    img  = Image.new("RGB", (config.VIDEO_WIDTH, config.VIDEO_HEIGHT), config.COLORS["bg"])

    # Banner zone: scale banner to full width, center-crop to BANNER_H
    banner   = Image.open(config.BANNER_PATH).convert("RGB")
    bw, bh   = banner.size
    scale    = config.VIDEO_WIDTH / bw
    new_bh   = int(bh * scale)
    banner   = banner.resize((config.VIDEO_WIDTH, new_bh), Image.LANCZOS)
    if new_bh > config.BANNER_H:
        top    = (new_bh - config.BANNER_H) // 2
        banner = banner.crop((0, top, config.VIDEO_WIDTH, top + config.BANNER_H))
    img.paste(banner, (0, config.BANNER_Y))

    draw = ImageDraw.Draw(img)

    # Hook / title zone
    try:
        font_hook = ImageFont.truetype(config.FONT_BOLD, 52)
    except OSError:
        font_hook = ImageFont.load_default()

    _draw_centered(draw, hook_text, font_hook,
                   config.TITLE_Y + config.TITLE_H // 2,
                   config.COLORS["accent"])

    # Hashtag zone
    try:
        font_hash = ImageFont.truetype(config.FONT_REGULAR, 34)
    except OSError:
        font_hash = ImageFont.load_default()

    _draw_centered(draw, hashtag_text, font_hash,
                   config.HASH_Y + config.HASH_H // 2,
                   config.COLORS["hashtag"])

    bg_path = config.TEMP_DIR / "background.png"
    img.save(bg_path)
    return bg_path


def compose_video(
    clip_path:   Path,
    bg_path:     Path,
    ass_path:    Path,
    output_path: Path,
    bgm_path:    Path,
    tts_path:    Path = None,
    duration:    int  = 45,
) -> None:
    """Composite background + clip + subtitles + audio into final MP4."""
    ass_esc = _ffmpeg_path(ass_path)

    video_filter = (
        f"[1:v]scale=1080:{config.CLIP_H}:force_original_aspect_ratio=decrease,"
        f"pad=1080:{config.CLIP_H}:(ow-iw)/2:(oh-ih)/2:black[clip];"
        f"[0:v][clip]overlay=0:{config.CLIP_Y}[vbase];"
        f"[vbase]subtitles='{ass_esc}':fontsdir='{_ffmpeg_path(config.ASSETS_DIR)}'[vout]"
    )

    if tts_path:
        # TTS (full volume) + BGM (background)
        audio_filter = (
            f"[2:a]volume=5.0[voice];"
            f"[3:a]afade=t=out:st={max(duration-5,0)}:d=5,volume=0.05[bgm];"
            f"[voice][bgm]amix=inputs=2:duration=first:dropout_transition=3:normalize=0[aout]"
        )
        inputs = [
            "-loop", "1", "-i", str(bg_path),
            "-i", str(clip_path),
            "-i", str(tts_path),
            "-i", str(bgm_path),
        ]
    else:
        audio_filter = (
            f"[2:a]afade=t=out:st={max(duration-5,0)}:d=5,volume=0.15[aout]"
        )
        inputs = [
            "-loop", "1", "-i", str(bg_path),
            "-i", str(clip_path),
            "-i", str(bgm_path),
        ]

    filter_complex = f"{video_filter};{audio_filter}"

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg compose failed:\n{result.stderr[-3000:]}")


# ── helpers ──────────────────────────────────────────────────────────────────

def _draw_centered(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    center_y: int,
    color: tuple,
) -> None:
    lines       = textwrap.wrap(text, width=22) or [text]
    line_height = font.size + 10
    total_h     = len(lines) * line_height
    y           = center_y - total_h // 2

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w    = bbox[2] - bbox[0]
        x    = (config.VIDEO_WIDTH - w) // 2
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0))   # shadow
        draw.text((x, y),         line, font=font, fill=color)
        y += line_height


def _ffmpeg_path(p: Path) -> str:
    """Convert a Windows path to a format safe for ffmpeg filter strings."""
    s = str(p).replace("\\", "/")
    # Escape drive-letter colon: C:/ → C\:/
    if len(s) >= 2 and s[1] == ":":
        s = s[0] + "\\:" + s[2:]
    return s
