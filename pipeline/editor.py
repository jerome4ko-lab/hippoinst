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
    duration:    int  = 55,
    gifs:        list = None,   # [{"path": Path, "start": float, "duration": float, "size": int}]
) -> None:
    """Composite background + clip + GIFs + subtitles + audio into final MP4."""
    ass_esc = _ffmpeg_path(ass_path)
    gifs = gifs or []

    # 4:3 중앙 크롭 → 1080x810로 리사이즈 → CLIP zone 중앙에 배치 (letterbox 없음)
    clip_w  = 1080
    clip_h  = clip_w * 3 // 4                       # 810
    clip_y  = config.CLIP_Y + (config.CLIP_H - clip_h) // 2  # 555

    # 인풋 순서: 0=bg, 1=clip, 2=[tts], 3=bgm, 4+=gifs
    inputs = ["-loop", "1", "-i", str(bg_path), "-i", str(clip_path)]
    if tts_path:
        inputs += ["-i", str(tts_path)]
        bgm_idx = 3
    else:
        bgm_idx = 2
    inputs += ["-i", str(bgm_path)]

    gif_idx_start = bgm_idx + 1
    for g in gifs:
        # 짧은 GIF는 stream_loop으로 자동 루프
        inputs += ["-stream_loop", "-1", "-i", str(g["path"])]

    # ── 비디오 필터 ─────────────────────────────────────────────────
    parts = [
        f"[1:v]crop='min(iw\\,ih*4/3)':ih,scale={clip_w}:{clip_h}[clip]",
        f"[0:v][clip]overlay=0:{clip_y}[vbase0]",
    ]
    cur = "vbase0"
    for i, g in enumerate(gifs):
        idx       = gif_idx_start + i
        size      = int(g.get("size") or 600)
        # 클립 영역 중앙(870)에서 GIF 중앙. Remotion GIF_CENTER_Y와 동일.
        gy        = config.CLIP_Y + config.CLIP_H // 2 - 90 - size // 2
        start     = float(g["start"])
        end       = start + float(g["duration"])
        next_lbl  = f"vbase{i+1}"
        parts.append(
            f"[{idx}:v]scale={size}:{size}:force_original_aspect_ratio=decrease[g{i}]"
        )
        parts.append(
            f"[{cur}][g{i}]overlay=(W-w)/2:{gy}:enable='between(t,{start:.2f},{end:.2f})'[{next_lbl}]"
        )
        cur = next_lbl

    parts.append(
        f"[{cur}]subtitles='{ass_esc}':fontsdir='{_ffmpeg_path(config.ASSETS_DIR)}'[vout]"
    )
    video_filter = ";".join(parts)

    # ── 오디오 필터 ─────────────────────────────────────────────────
    if tts_path:
        audio_filter = (
            f"[2:a]volume=5.0[voice];"
            f"[{bgm_idx}:a]afade=t=out:st={max(duration-5,0)}:d=5,volume=0.08[bgm];"
            f"[voice][bgm]amix=inputs=2:duration=first:dropout_transition=3:normalize=0[aout]"
        )
    else:
        audio_filter = (
            f"[{bgm_idx}:a]afade=t=out:st={max(duration-5,0)}:d=5,volume=0.15[aout]"
        )

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
