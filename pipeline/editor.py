import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import config


def create_background_frame(
    hook_text: str,
    *,
    pill_text: str = "",
    clipless: bool = False,
) -> Path:
    """Render static background as PNG: 검정 타이틀 블록 + 자막 띠 + 클립 영역.

    레이아웃:
      [0 ~ TITLE_H]      검정 배경 + (선택)노란 알약 + 흰색 메인 + 노란 강조
      [SUB_Y ~ +SUB_H]   검정 자막 띠 (ASS가 위에 그림)
      [CLIP_Y ~ 1920]    클립 영역. 클립 있으면 영상이 덮음. 없으면 그라디언트 + 큰 hook.

    hook_text에 `|` 또는 `\\n`이 있으면 앞=흰색 메인, 뒤=노란 강조로 분리.
    """
    config.TEMP_DIR.mkdir(parents=True, exist_ok=True)

    img  = Image.new("RGB", (config.VIDEO_WIDTH, config.VIDEO_HEIGHT), config.COLORS["bg"])

    # 클립 영역: 클립 없을 때 보일 그라디언트 (클립 있으면 영상이 덮음)
    _paint_vertical_gradient(
        img,
        x=0, y=config.CLIP_Y,
        w=config.VIDEO_WIDTH, h=config.CLIP_H,
        top_rgb=config.COLORS["banner_bg"],
        bot_rgb=config.COLORS["bg"],
    )

    draw = ImageDraw.Draw(img)
    main_text, _ = _draw_title_block(draw, hook_text, pill_text)

    # Clipless 모드: 클립 영역 중앙에 큰 hook 다시 (비주얼 앵커)
    if clipless:
        try:
            font_big = ImageFont.truetype(config.FONT_BOLD, 88)
        except OSError:
            font_big = ImageFont.load_default()
        anchor_y = config.CLIP_Y + config.CLIP_H // 2
        _draw_centered(draw, main_text, font_big, anchor_y, config.COLORS["text"])

    bg_path = config.TEMP_DIR / "background.png"
    img.save(bg_path)
    return bg_path


def create_template_preview(hook_text: str, *, pill_text: str = "") -> Path:
    """쇼츠 템플릿 미리보기 PNG. 영상·TTS·ffmpeg 없이 즉시 생성.

    실제 영상의 영역(타이틀 / 클립 / 자막)에 더해, 미리보기에서만 YouTube
    Shorts에서 자동 오버레이되는 UI(제품 보기 / 채널·구독 / 캡션 / 설명)
    를 흐리게 가상으로 표시한다.
    """
    config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    img  = Image.new("RGB", (config.VIDEO_WIDTH, config.VIDEO_HEIGHT), config.COLORS["bg"])
    draw = ImageDraw.Draw(img)

    # 1) 타이틀 블록 (실제 렌더와 동일)
    _draw_title_block(draw, hook_text, pill_text)

    # 2) CLIP 영역 가이드 (4:3, 1080×810)
    _paint_vertical_gradient(
        img,
        x=0, y=config.CLIP_Y,
        w=config.VIDEO_WIDTH, h=config.CLIP_H,
        top_rgb=config.COLORS["banner_bg"],
        bot_rgb=config.COLORS["bg"],
    )
    _draw_dashed_rect(
        draw, x=12, y=config.CLIP_Y + 12,
        w=config.VIDEO_WIDTH - 24, h=config.CLIP_H - 24,
        color=(220, 220, 240), dash=18, gap=12, width=4,
    )
    try:
        font_clip_main = ImageFont.truetype(config.FONT_BOLD, 72)
        font_clip_sub  = ImageFont.truetype(config.FONT_REGULAR, 36)
    except OSError:
        font_clip_main = font_clip_sub = ImageFont.load_default()
    cx = config.CLIP_Y + config.CLIP_H // 2
    _draw_centered(draw, "비디오 클립 영역", font_clip_main, cx - 30, config.COLORS["text"])
    _draw_centered(
        draw, f"{config.VIDEO_WIDTH} × {config.CLIP_H} px  (4:3)",
        font_clip_sub, cx + 60, (200, 200, 220),
    )

    # 3) SUB 띠 가이드 (클립 아래)
    _draw_dashed_rect(
        draw, x=12, y=config.SUB_Y + 6,
        w=config.VIDEO_WIDTH - 24, h=config.SUB_H - 12,
        color=(110, 110, 130), dash=14, gap=10, width=3,
    )
    try:
        font_label = ImageFont.truetype(config.FONT_REGULAR, 24)
        font_sample = ImageFont.truetype(config.FONT_BOLD, config.SUBTITLE_FONT_SIZE)
    except OSError:
        font_label = font_sample = ImageFont.load_default()
    draw.text((28, config.SUB_Y + 12), "자막 영역", font=font_label, fill=(160, 160, 180))
    _draw_centered(
        draw, "여기에 자막이 표시됩니다", font_sample,
        config.SUB_Y + config.SUB_H // 2, config.COLORS["text"],
    )

    # 4) YouTube UI 가상 오버레이 (미리보기 전용)
    _draw_youtube_ui_mockup(draw)

    out_path = config.TEMP_DIR / "template_preview.png"
    img.save(out_path)
    return out_path


def _draw_youtube_ui_mockup(draw: ImageDraw.ImageDraw) -> None:
    """미리보기 전용 — 실제 YouTube Shorts에서 자동 오버레이되는 UI 위치를 흐리게 표시."""
    FADED  = (170, 170, 190)
    FADED2 = (120, 120, 140)
    PILL_BG = (220, 220, 230)
    SUB_BTN = (200, 50, 60)   # YouTube 빨강 (faded)

    try:
        font_pp    = ImageFont.truetype(config.FONT_BOLD, 30)
        font_ch    = ImageFont.truetype(config.FONT_BOLD, 36)
        font_sub   = ImageFont.truetype(config.FONT_BOLD, 28)
        font_cap   = ImageFont.truetype(config.FONT_BOLD, 32)
        font_desc  = ImageFont.truetype(config.FONT_REGULAR, 28)
        font_label = ImageFont.truetype(config.FONT_REGULAR, 22)
        font_rail  = ImageFont.truetype(config.FONT_BOLD, 24)
    except OSError:
        font_pp = font_ch = font_sub = font_cap = font_desc = font_label = font_rail = ImageFont.load_default()

    # ── (A) "제품 보기" — 클립 영역 좌하단 오버레이
    pp_text = "▷ 제품 보기"
    bbox = draw.textbbox((0, 0), pp_text, font=font_pp)
    pp_tw, pp_th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pp_pad_x, pp_pad_y = 24, 14
    pp_w = pp_tw + pp_pad_x * 2
    pp_h = pp_th + pp_pad_y * 2
    pp_x = 40
    pp_y = config.CLIP_Y + config.CLIP_H - pp_h - 28
    draw.rounded_rectangle(
        (pp_x, pp_y, pp_x + pp_w, pp_y + pp_h),
        radius=pp_h // 2, fill=PILL_BG,
    )
    draw.text(
        (pp_x + pp_pad_x - bbox[0], pp_y + pp_pad_y - bbox[1]),
        pp_text, font=font_pp, fill=(40, 40, 50),
    )

    # ── (B) 우측 액션 레일 (Like / Comment / Share / Music)
    rail_x = config.VIDEO_WIDTH - 90
    rail_top = config.CLIP_Y + 60
    rail_step = 130
    for i, label in enumerate(["♥", "댓", "↗", "♪"]):
        cy = rail_top + i * rail_step
        # 원형 배경
        draw.ellipse((rail_x - 36, cy - 36, rail_x + 36, cy + 36),
                     outline=FADED, width=2)
        bb = draw.textbbox((0, 0), label, font=font_cap)
        tw = bb[2] - bb[0]; th = bb[3] - bb[1]
        draw.text((rail_x - tw // 2 - bb[0], cy - th // 2 - bb[1]),
                  label, font=font_cap, fill=FADED)
        draw.text((rail_x - 18, cy + 44), "12k", font=font_rail, fill=FADED2)

    # ── (C) 채널 행 (avatar + @hippoinst + 구독)  — SUB 띠 아래
    ch_y = config.SUB_Y + config.SUB_H + 30
    av_r = 32
    av_cx = 40 + av_r
    av_cy = ch_y + av_r
    draw.ellipse((av_cx - av_r, av_cy - av_r, av_cx + av_r, av_cy + av_r), fill=FADED2)

    name_text = "@hippoinst"
    name_x = av_cx + av_r + 18
    name_bb = draw.textbbox((0, 0), name_text, font=font_ch)
    name_h = name_bb[3] - name_bb[1]
    name_y = av_cy - name_h // 2 - name_bb[1]
    draw.text((name_x, name_y), name_text, font=font_ch, fill=FADED)

    name_w = name_bb[2] - name_bb[0]
    sb_x = name_x + name_w + 24
    sb_text = "구독"
    sb_bb = draw.textbbox((0, 0), sb_text, font=font_sub)
    sb_tw = sb_bb[2] - sb_bb[0]; sb_th = sb_bb[3] - sb_bb[1]
    sb_pad_x, sb_pad_y = 22, 12
    sb_w = sb_tw + sb_pad_x * 2
    sb_h = sb_th + sb_pad_y * 2
    sb_y = av_cy - sb_h // 2
    draw.rounded_rectangle((sb_x, sb_y, sb_x + sb_w, sb_y + sb_h),
                           radius=sb_h // 2, fill=SUB_BTN)
    draw.text((sb_x + sb_pad_x - sb_bb[0], sb_y + sb_pad_y - sb_bb[1]),
              sb_text, font=font_sub, fill=(245, 245, 250))

    # ── (D) ▶ 캡션
    cap_text = "▶ 명창이라는 데이식스 팬들 떼창"
    draw.text((40, ch_y + av_r * 2 + 30), cap_text, font=font_cap, fill=FADED)

    # ── (E) 하단 설명 (해시태그 포함)
    desc_text = "윤하 파트 뺏어 부른 자의 최후..   #우산"
    draw.text((40, ch_y + av_r * 2 + 90), desc_text, font=font_desc, fill=FADED2)

    # ── (F) 안내 라벨
    draw.text(
        (40, config.VIDEO_HEIGHT - 50),
        "↑ YouTube 자동 표시 영역 (실제 영상에는 표시되지 않음)",
        font=font_label, fill=(100, 100, 120),
    )


def _draw_title_block(
    draw: ImageDraw.ImageDraw,
    hook_text: str,
    pill_text: str = "",
) -> tuple[str, str]:
    """알약 + 메인(흰) + 강조(노랑) 두 줄 타이틀을 그리고 (main, accent)를 반환."""
    if pill_text and pill_text.strip():
        _draw_pill(
            draw,
            text=pill_text.strip(),
            center_x=config.VIDEO_WIDTH // 2,
            center_y=config.PILL_Y + config.PILL_H // 2,
            slot_h=config.PILL_H,
        )

    main_text, accent_text = _split_hook(hook_text)

    try:
        font_main   = ImageFont.truetype(config.FONT_BOLD, 88)
        font_accent = ImageFont.truetype(config.FONT_BOLD, 96)
    except OSError:
        font_main = font_accent = ImageFont.load_default()

    main_center_y   = config.TITLE_Y + int(config.TITLE_H * 0.50)
    accent_center_y = config.TITLE_Y + int(config.TITLE_H * 0.78)

    _draw_centered(draw, main_text, font_main, main_center_y, config.COLORS["text"])
    if accent_text:
        _draw_centered(draw, accent_text, font_accent, accent_center_y, config.COLORS["accent"])
    return main_text, accent_text


def _draw_dashed_rect(
    draw: ImageDraw.ImageDraw,
    *, x: int, y: int, w: int, h: int,
    color: tuple, dash: int = 14, gap: int = 10, width: int = 3,
) -> None:
    """점선 직사각형. PIL에 점선 함수가 없어 직접 짧은 선분 반복."""
    step = dash + gap
    # 위·아래
    for sx in range(x, x + w, step):
        ex = min(sx + dash, x + w)
        draw.line([(sx, y), (ex, y)], fill=color, width=width)
        draw.line([(sx, y + h), (ex, y + h)], fill=color, width=width)
    # 좌·우
    for sy in range(y, y + h, step):
        ey = min(sy + dash, y + h)
        draw.line([(x, sy), (x, ey)], fill=color, width=width)
        draw.line([(x + w, sy), (x + w, ey)], fill=color, width=width)


def _split_hook(hook_text: str) -> tuple[str, str]:
    """hook을 (메인, 강조) 두 부분으로 나눈다. 구분자가 없으면 강조는 빈 문자열."""
    if not hook_text:
        return "", ""
    for sep in ("\n", "|"):
        if sep in hook_text:
            head, _, tail = hook_text.partition(sep)
            return head.strip(), tail.strip()
    return hook_text.strip(), ""


def _draw_pill(
    draw: ImageDraw.ImageDraw,
    *,
    text: str,
    center_x: int,
    center_y: int,
    slot_h: int,
) -> None:
    """노란 둥근 알약 + 짙은 글씨."""
    font_size = max(28, slot_h - 36)
    try:
        font = ImageFont.truetype(config.FONT_BOLD, font_size)
    except OSError:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    tw   = bbox[2] - bbox[0]
    th   = bbox[3] - bbox[1]

    pad_x = 32
    pad_y = 14
    box_w = tw + pad_x * 2
    box_h = th + pad_y * 2
    x0    = center_x - box_w // 2
    y0    = center_y - box_h // 2
    radius = box_h // 2

    draw.rounded_rectangle(
        (x0, y0, x0 + box_w, y0 + box_h),
        radius=radius,
        fill=config.COLORS["accent"],
    )
    # 텍스트는 textbbox의 음수 오프셋(bbox[0], bbox[1])을 보정해서 정확히 가운데
    tx = x0 + pad_x - bbox[0]
    ty = y0 + pad_y - bbox[1]
    draw.text((tx, ty), text, font=font, fill=config.COLORS["pill_text"])


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

    # 4:3 중앙 크롭 (1080×810) → CLIP 영역 안에 가운데 정렬
    clip_w  = config.VIDEO_WIDTH                                 # 1080
    clip_h  = clip_w * 3 // 4                                    # 810
    clip_y  = config.CLIP_Y + (config.CLIP_H - clip_h) // 2      # CLIP_H=810이면 그대로 CLIP_Y

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
    next_idx += len(gifs)

    # 캐릭터 립싱크 PNG 시퀀스 (TTS 있을 때만; 의존성·에셋·토글 미충족 시 None)
    char_idx = None
    if tts_path:
        try:
            from pipeline import character as _character
            char_dir = config.TEMP_DIR / "character_frames" / output_path.stem
            char_path = _character.render_character_frames(Path(tts_path), char_dir, fps=30)
        except Exception as e:
            print(f"[character] skip — {e}")
            char_path = None
        if char_path is not None:
            pattern = str(char_path / "character_%06d.png")
            inputs += ["-framerate", "30", "-start_number", "1", "-i", pattern]
            char_idx = next_idx
            next_idx += 1

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

    if char_idx is not None:
        # 자막 → vsubs → PNG 시퀀스(RGBA) 캐릭터 오버레이 → vout
        parts.append(
            f"[{cur}]subtitles='{ass_esc}':fontsdir='{_ffmpeg_path(config.ASSETS_DIR)}'[vsubs]"
        )
        cs = int(config.CHARACTER_SIZE)
        # 클립 우하단 코너에 정확히 정렬 (캐릭터 하단 = 클립 하단).
        # x는 우측 100px 안쪽 — YouTube 좋아요/싫어요 버튼과 겹치지 않도록.
        char_y = config.CLIP_Y + config.CLIP_H - cs
        # PNG 시퀀스는 입력 시 이미 rgba. scale 뒤에도 rgba 유지해야 alpha 보존.
        parts.append(f"[{char_idx}:v]scale={cs}:{cs},format=rgba[char]")
        parts.append(f"[vsubs][char]overlay=W-w-100:{char_y}:shortest=0[vout]")
    else:
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
            f"[voice][bgm]amix=inputs=2:duration=longest:dropout_transition=3:normalize=0[mix];"
            # 합쳐진 후 -1 dBTP로 천장 잡아 클리핑 방지
            f"[mix]alimiter=limit=0.89:level=disabled[aout]"
        )
    else:
        audio_filter = (
            f"[{bgm_idx}:a]afade=t=out:st={fade_st}:d=5,volume={bv_alone:.3f},"
            f"alimiter=limit=0.89:level=disabled[aout]"
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
