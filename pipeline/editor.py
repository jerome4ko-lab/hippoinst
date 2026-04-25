import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import config


def create_background_frame(hook_text: str, hashtag_text: str, *, clipless: bool = False) -> Path:
    """Render static background (banner + hook title + hashtag) as PNG.

    clip 영역(CLIP_Y~CLIP_Y+CLIP_H)에는 항상 미묘한 vertical gradient를 깔아둔다.
    클립이 있으면 영상이 위에 덮이므로 무해. clipless=True 일 땐 추가로 클립 영역 중앙에
    큰 hook 텍스트를 그려 비주얼 앵커로 사용.
    """
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

    # Clip zone: vertical gradient (banner_bg → bg). 클립 있을 땐 영상이 덮음.
    _paint_vertical_gradient(
        img,
        x=0, y=config.CLIP_Y,
        w=config.VIDEO_WIDTH, h=config.CLIP_H,
        top_rgb=config.COLORS["banner_bg"],
        bot_rgb=config.COLORS["bg"],
    )

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

    # Clipless 모드: 클립 영역 위쪽 1/3 지점에 큰 hook 한 번 더 (비주얼 앵커)
    if clipless:
        try:
            font_big = ImageFont.truetype(config.FONT_BOLD, 88)
        except OSError:
            font_big = ImageFont.load_default()
        anchor_y = config.CLIP_Y + config.CLIP_H // 3
        _draw_centered(draw, hook_text, font_big, anchor_y, config.COLORS["text"])

    bg_path = config.TEMP_DIR / "background.png"
    img.save(bg_path)
    return bg_path


def _paint_vertical_gradient(
    img: Image.Image,
    x: int, y: int, w: int, h: int,
    top_rgb: tuple, bot_rgb: tuple,
) -> None:
    """이미지 [x,y,w,h] 영역을 top_rgb→bot_rgb 수직 그라디언트로 칠한다."""
    grad = Image.new("RGB", (1, h))
    for i in range(h):
        t = i / max(h - 1, 1)
        c = tuple(int(top_rgb[k] + (bot_rgb[k] - top_rgb[k]) * t) for k in range(3))
        grad.putpixel((0, i), c)
    grad = grad.resize((w, h), Image.NEAREST)
    img.paste(grad, (x, y))


def compose_video(
    clip_path:   Path | None,
    bg_path:     Path,
    ass_path:    Path,
    output_path: Path,
    bgm_path:    Path,
    tts_path:    Path = None,
    duration:    int  = 55,
    gifs:        list = None,   # [{"path": Path, "start": float, "duration": float, "size": int}]
    voice_gain:  float = None,  # None → ElevenLabs 디폴트 사용
    bgm_volume:  float = None,
) -> None:
    """Composite background + clip + GIFs + subtitles + audio into final MP4.

    clip_path가 None이면 클립 합성 단계를 건너뛰고 배경(그라디언트+큰 hook)만 사용.
    """
    ass_esc = _ffmpeg_path(ass_path)
    gifs = gifs or []
    has_clip = clip_path is not None

    # 4:3 중앙 크롭 → 1080x810 (클립 있을 때만 사용)
    clip_w  = 1080
    clip_h  = clip_w * 3 // 4                       # 810
    clip_y  = config.CLIP_Y + (config.CLIP_H - clip_h) // 2  # 555

    # 인풋 인덱스 동적 할당
    inputs = ["-loop", "1", "-i", str(bg_path)]   # 0 = bg
    next_idx = 1
    if has_clip:
        inputs += ["-i", str(clip_path)]
        next_idx += 1
    if tts_path:
        inputs += ["-i", str(tts_path)]
        tts_idx = next_idx
        next_idx += 1
    else:
        tts_idx = None
    inputs += ["-i", str(bgm_path)]
    bgm_idx = next_idx
    next_idx += 1

    gif_idx_start = next_idx
    for g in gifs:
        # 짧은 GIF는 stream_loop으로 자동 루프
        inputs += ["-stream_loop", "-1", "-i", str(g["path"])]

    # ── 비디오 필터 ─────────────────────────────────────────────────
    parts: list[str] = []
    if has_clip:
        parts.append(
            f"[1:v]crop='min(iw\\,ih*4/3)':ih,scale={clip_w}:{clip_h}[clip]"
        )
        parts.append(
            f"[0:v][clip]overlay=0:{clip_y}[vbase0]"
        )
        cur = "vbase0"
    else:
        # 배경 PNG가 그대로 vbase0. format=yuv420p로 정렬해 후속 overlay 정상.
        parts.append("[0:v]format=yuv420p[vbase0]")
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
    vg  = float(voice_gain if voice_gain is not None else config.TTS_VOICE_GAIN["elevenlabs"])
    bv  = float(bgm_volume if bgm_volume is not None else config.BGM_VOLUME)
    bv_alone = float(bgm_volume if bgm_volume is not None else config.BGM_VOLUME_NO_VOICE)
    fade_st  = max(duration - 5, 0)

    if tts_path:
        audio_filter = (
            f"[{tts_idx}:a]volume={vg:.3f}[voice];"
            f"[{bgm_idx}:a]afade=t=out:st={fade_st}:d=5,volume={bv:.3f}[bgm];"
            f"[voice][bgm]amix=inputs=2:duration=first:dropout_transition=3:normalize=0[aout]"
        )
    else:
        audio_filter = (
            f"[{bgm_idx}:a]afade=t=out:st={fade_st}:d=5,volume={bv_alone:.3f}[aout]"
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
