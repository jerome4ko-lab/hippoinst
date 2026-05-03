import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
import config


# 제목 텍스트 블러 그림자 — 배경(bg_laser)과의 블렌딩용
TITLE_SHADOW_RGBA = (26, 26, 46, 179)   # #1a1a2e, 70% 알파
TITLE_SHADOW_BLUR = 8                    # GaussianBlur radius (시각 번짐 ~16-20px)
# 타이틀 영역 하단 그라데이션 스트립 (#0e0e0e → 투명, 30px)
TITLE_BOTTOM_GRAD_H        = 30
TITLE_BOTTOM_GRAD_TOP_RGBA = (14, 14, 14, 255)
TITLE_BOTTOM_GRAD_BOT_RGBA = (14, 14, 14, 0)

TITLE_SIDE_MARGIN       = 72
TITLE_BOTTOM_PADDING    = 30
TITLE_TEXT_GAP          = 14
TITLE_MAIN_FONT_SIZE    = 88
TITLE_ACCENT_FONT_SIZE  = 96
TITLE_MIN_FONT_SCALE    = 0.62
TITLE_MAIN_MAX_LINES    = 2
TITLE_ACCENT_MAX_LINES  = 2
CHARACTER_BELOW_SUBTITLE_GAP = 24  # fallback when config.py has no explicit value


def create_background_frame(
    hook_text: str,
    *,
    pill_text: str = "",
    clipless: bool = False,
    hook_accent_color: str | None = None,
    bg_template: str = "bg_purple",
) -> Path:
    """Render static background as PNG.

    레이아웃 (bg_laser 위 합성):
      [0 ~ 1920]            assets/bg_laser.png 이 베이스 (자동 생성 폴백 있음)
      [TITLE_Y ~ TITLE_H]   타이틀(블러 다크네이비 그림자 → 텍스트), 마지막 30px 그라데이션 페이드
      [CLIP_Y ~ 1920]       클립 자리 — 클립이 있으면 compose_video 단계에서 페더링 후 덮음

    hook_text에 `|` 또는 `\\n`이 있으면 앞=흰색 메인, 뒤=선택 강조색으로 분리.
    """
    config.TEMP_DIR.mkdir(parents=True, exist_ok=True)

    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    accent_rgb = _coerce_hex_rgb(hook_accent_color, config.COLORS["accent"])

    bg_laser = config.BG_TEMPLATE_MAP.get(bg_template, config.BG_TEMPLATE_FALLBACK)
    if not bg_laser.exists():
        raise FileNotFoundError(
            f"배경 이미지가 없습니다: {bg_laser} — assets/bg_template/ 폴더에 png를 넣어주세요."
        )

    base = Image.open(bg_laser).convert("RGB")
    if base.size != (W, H):
        base = base.resize((W, H), Image.LANCZOS)
    canvas = base.convert("RGBA")

    # 1) 제목 텍스트 블러 그림자 — 라스 배경 위에서도 본문이 살아있게
    shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    has_pill = bool(pill_text and pill_text.strip())

    main_text, accent_text = _draw_title_text(
        ImageDraw.Draw(shadow_layer),
        hook_text,
        main_color=TITLE_SHADOW_RGBA,
        accent_color=TITLE_SHADOW_RGBA,
        has_pill=has_pill,
    )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(TITLE_SHADOW_BLUR))
    canvas = Image.alpha_composite(canvas, shadow_layer)

    # 2) 알약(불투명) + 본 제목 텍스트 (그림자 위에 또렷하게)
    title_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if has_pill:
        _draw_pill(
            ImageDraw.Draw(title_layer),
            text=pill_text.strip(),
            center_x=W // 2,
            center_y=config.PILL_Y + config.PILL_H // 2,
            slot_h=config.PILL_H,
        )
    _draw_title_text(
        ImageDraw.Draw(title_layer),
        hook_text,
        main_color=config.COLORS["text"] + (255,),
        accent_color=accent_rgb + (255,),
        has_pill=has_pill,
    )
    canvas = Image.alpha_composite(canvas, title_layer)

    # 3) Clipless 모드: 클립 영역 중앙에 큰 hook 다시 (비주얼 앵커)
    if clipless:
        try:
            font_big = ImageFont.truetype(config.FONT_BOLD, 88)
        except OSError:
            font_big = ImageFont.load_default()
        anchor_y = config.CLIP_Y + config.CLIP_H // 2
        _draw_centered(ImageDraw.Draw(canvas), main_text, font_big, anchor_y, config.COLORS["text"])

    bg_path = config.TEMP_DIR / "background.png"
    canvas.convert("RGB").save(bg_path)
    return bg_path


def create_template_preview(
    hook_text: str,
    *,
    pill_text: str = "",
    hook_accent_color: str | None = None,
    bg_template: str = "bg_purple",
) -> Path:
    """쇼츠 템플릿 미리보기 PNG. 영상·TTS·ffmpeg 없이 즉시 생성.

    실제 영상의 영역(타이틀 / 클립 / 자막)에 더해, 미리보기에서만 YouTube
    Shorts에서 자동 오버레이되는 UI(제품 보기 / 채널·구독 / 캡션 / 설명)
    를 흐리게 가상으로 표시한다.
    """
    config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    bg_laser = config.BG_TEMPLATE_MAP.get(bg_template, config.BG_TEMPLATE_FALLBACK)
    if bg_laser.exists():
        img = Image.open(bg_laser).convert("RGB")
        if img.size != (config.VIDEO_WIDTH, config.VIDEO_HEIGHT):
            img = img.resize((config.VIDEO_WIDTH, config.VIDEO_HEIGHT), Image.LANCZOS)
    else:
        img = Image.new("RGB", (config.VIDEO_WIDTH, config.VIDEO_HEIGHT), config.COLORS["bg"])
    draw = ImageDraw.Draw(img)

    # 1) 타이틀 블록 (실제 렌더와 동일)
    _draw_title_block(draw, hook_text, pill_text, hook_accent_color=hook_accent_color)

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
    hook_accent_color: str | None = None,
) -> None:
    """Draw the title area used by the static template preview."""
    has_pill = bool(pill_text and pill_text.strip())
    if has_pill:
        _draw_pill(
            draw,
            text=pill_text.strip(),
            center_x=config.VIDEO_WIDTH // 2,
            center_y=config.PILL_Y + config.PILL_H // 2,
            slot_h=config.PILL_H,
        )
    _draw_title_text(
        draw,
        hook_text,
        main_color=config.COLORS["text"],
        accent_color=_coerce_hex_rgb(hook_accent_color, config.COLORS["accent"]),
        has_pill=has_pill,
    )


def _draw_title_text(
    draw: ImageDraw.ImageDraw,
    hook_text: str,
    *,
    main_color: tuple,
    accent_color: tuple,
    has_pill: bool = False,
) -> tuple[str, str]:
    """메인(흰) + 강조(노랑) 두 줄 타이틀을 그리고 (main, accent)를 반환.

    그림자 패스에서는 main/accent 둘 다 그림자색을 넘겨 같은 글자 모양으로 블러를 만든다.
    """
    main_text, accent_text = _split_hook(hook_text)

    layout = _fit_title_layout(draw, main_text, accent_text, has_pill=has_pill)
    y = layout["top"]
    for line in layout["main_lines"]:
        _draw_centered_plain_line(draw, line, layout["main_font"], y, main_color)
        y += layout["main_line_h"]
    if layout["accent_lines"]:
        y += TITLE_TEXT_GAP
        for line in layout["accent_lines"]:
            _draw_centered_plain_line(draw, line, layout["accent_font"], y, accent_color)
            y += layout["accent_line_h"]
    return main_text, accent_text


def _fit_title_layout(
    draw: ImageDraw.ImageDraw,
    main_text: str,
    accent_text: str,
    *,
    has_pill: bool,
) -> dict:
    max_width = config.VIDEO_WIDTH - TITLE_SIDE_MARGIN * 2
    top_limit = (
        config.PILL_Y + config.PILL_H + 22
        if has_pill
        else config.TITLE_Y + 78
    )
    bottom_limit = config.CLIP_Y - TITLE_BOTTOM_PADDING
    available_h = max(120, bottom_limit - top_limit)

    fallback = None
    for pct in range(100, int(TITLE_MIN_FONT_SCALE * 100) - 1, -2):
        scale = pct / 100
        main_font = _load_title_font(max(1, round(TITLE_MAIN_FONT_SIZE * scale)))
        accent_font = _load_title_font(max(1, round(TITLE_ACCENT_FONT_SIZE * scale)))
        main_lines = _wrap_text_to_width(draw, main_text, main_font, max_width) or [""]
        accent_lines = _wrap_text_to_width(draw, accent_text, accent_font, max_width) if accent_text else []

        main_ok = len(main_lines) <= TITLE_MAIN_MAX_LINES
        accent_ok = len(accent_lines) <= TITLE_ACCENT_MAX_LINES
        main_line_h = int(main_font.size * 1.08) if hasattr(main_font, "size") else 96
        accent_line_h = int(accent_font.size * 1.05) if hasattr(accent_font, "size") else 100
        total_h = len(main_lines) * main_line_h
        if accent_lines:
            total_h += TITLE_TEXT_GAP + len(accent_lines) * accent_line_h

        layout = {
            "main_font": main_font,
            "accent_font": accent_font,
            "main_lines": main_lines,
            "accent_lines": accent_lines,
            "main_line_h": main_line_h,
            "accent_line_h": accent_line_h,
            "total_h": total_h,
        }
        fallback = layout
        if main_ok and accent_ok and total_h <= available_h:
            layout["top"] = top_limit + (available_h - total_h) // 2
            return layout

    assert fallback is not None
    fallback["main_lines"] = _limit_lines(
        draw, fallback["main_lines"], fallback["main_font"], TITLE_MAIN_MAX_LINES, max_width
    )
    fallback["accent_lines"] = _limit_lines(
        draw, fallback["accent_lines"], fallback["accent_font"], TITLE_ACCENT_MAX_LINES, max_width
    )
    total_h = len(fallback["main_lines"]) * fallback["main_line_h"]
    if fallback["accent_lines"]:
        total_h += TITLE_TEXT_GAP + len(fallback["accent_lines"]) * fallback["accent_line_h"]
    fallback["top"] = top_limit + max(0, (available_h - total_h) // 2)
    return fallback


def _load_title_font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(config.FONT_BOLD, size)
    except OSError:
        return ImageFont.load_default()


def _wrap_text_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []

    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = word if not current else f"{current} {word}"
        if _text_width(draw, candidate, font) <= max_width:
            current = candidate
            continue

        if current:
            lines.append(current)
        if _text_width(draw, word, font) <= max_width:
            current = word
            continue

        chunk = ""
        for ch in word:
            candidate = f"{chunk}{ch}"
            if chunk and _text_width(draw, candidate, font) > max_width:
                lines.append(chunk)
                chunk = ch
            else:
                chunk = candidate
        current = chunk

    if current:
        lines.append(current)
    return lines


def _limit_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    max_lines: int,
    max_width: int,
) -> list[str]:
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    kept[-1] = _ellipsize_to_width(draw, " ".join(lines[max_lines - 1:]), font, max_width)
    return kept


def _ellipsize_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> str:
    suffix = "..."
    text = text.strip()
    while text and _text_width(draw, text + suffix, font) > max_width:
        text = text[:-1].rstrip()
    return (text + suffix) if text else suffix


def _text_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _draw_centered_plain_line(
    draw: ImageDraw.ImageDraw,
    line: str,
    font: ImageFont.FreeTypeFont,
    y: int,
    color: tuple,
) -> None:
    """제목 영역용 단일 fill 라인. 그림자는 별도 blur layer에서 처리한다."""
    bbox = draw.textbbox((0, 0), line, font=font)
    w = bbox[2] - bbox[0]
    x = (config.VIDEO_WIDTH - w) // 2
    draw.text((x, y - bbox[1]), line, font=font, fill=color)


def _draw_centered_plain(
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
        _draw_centered_plain_line(draw, line, font, y, color)
        y += line_height


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


def _coerce_hex_rgb(value: str | None, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    if not value:
        return fallback
    s = str(value).strip().lstrip("#")
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        return fallback
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


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


def _make_alpha_gradient_layer(
    canvas_w: int, canvas_h: int,
    *, x: int, y: int, w: int, h: int,
    top_rgba: tuple, bot_rgba: tuple,
) -> Image.Image:
    """투명 RGBA 캔버스에 (x,y,w,h) 위치로 수직 알파 그라디언트 스트립을 얹어 반환."""
    layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    strip = Image.new("RGBA", (1, h))
    for i in range(h):
        t = i / max(h - 1, 1)
        c = tuple(int(top_rgba[k] + (bot_rgba[k] - top_rgba[k]) * t) for k in range(4))
        strip.putpixel((0, i), c)
    strip = strip.resize((w, h), Image.NEAREST)
    layer.paste(strip, (x, y))
    return layer


def _ensure_clip_feather_mask(clip_w: int, clip_h: int, feather: int) -> Path:
    """클립 4테두리 페더링 알파 마스크 — 한 번 만들어 캐시. ffmpeg alphamerge 의 source 로 쓴다."""
    out = config.TEMP_DIR / f"clip_feather_{clip_w}x{clip_h}_{feather}.png"
    if out.exists() and out.stat().st_size > 0:
        return out

    config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    inset = max(0, feather)
    mask = Image.new("L", (clip_w, clip_h), 0)
    md = ImageDraw.Draw(mask)
    md.rectangle(
        (inset, inset, clip_w - inset, clip_h - inset),
        fill=255,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(max(1, feather // 2)))
    mask.save(out)
    return out


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
    clip_volume: float | None = None,  # None이면 클립 오디오 무시(기존 동작)
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
    clip_idx = mask_idx = None
    if has_clip:
        inputs += ["-i", str(clip_path)]
        clip_idx = next_idx
        next_idx += 1
        feather_px = int(getattr(config, "CLIP_FEATHER_PX", 50))
        mask_path  = _ensure_clip_feather_mask(clip_w, clip_h, feather_px)
        inputs += ["-loop", "1", "-i", str(mask_path)]
        mask_idx = next_idx
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
            print(f"[character] skip - {e}")
            char_path = None
        if char_path is not None:
            pattern = str(char_path / "character_%06d.png")
            inputs += ["-framerate", "30", "-start_number", "1", "-i", pattern]
            char_idx = next_idx
            next_idx += 1

    # ── 비디오 필터 ─────────────────────────────────────────────────
    parts: list[str] = []
    if has_clip:
        # 클립 RGB → 알파 채널을 페더링 마스크로 교체 → 배경(bg_laser)과 부드럽게 블렌딩
        parts.append(
            f"[{clip_idx}:v]crop='min(iw\\,ih*4/3)':ih,"
            f"scale={clip_w}:{clip_h},setsar=1,format=yuva420p[clipraw]"
        )
        parts.append(
            f"[{mask_idx}:v]scale={clip_w}:{clip_h},format=gray[clipmask]"
        )
        parts.append("[clipraw][clipmask]alphamerge[clip]")
        parts.append(
            f"[0:v][clip]overlay=0:{clip_y}:format=auto[vbase0]"
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
        # 자막 띠 아래쪽에 배치해 자막과 겹치지 않게 한다.
        # x는 우측 CHARACTER_RIGHT_INSET px 안쪽 — YouTube 좋아요/싫어요 버튼과 겹치지 않도록.
        below_subtitle_gap = int(
            getattr(config, "CHARACTER_BELOW_SUBTITLE_GAP", CHARACTER_BELOW_SUBTITLE_GAP)
        )
        char_y = min(
            config.SUB_Y + config.SUB_H + below_subtitle_gap,
            config.VIDEO_HEIGHT - cs,
        )
        right_inset = int(getattr(config, "CHARACTER_RIGHT_INSET", 90))
        # PNG 시퀀스는 입력 시 이미 rgba. scale 뒤에도 rgba 유지해야 alpha 보존.
        parts.append(f"[{char_idx}:v]scale={cs}:{cs},format=rgba[char]")
        parts.append(f"[vsubs][char]overlay=W-w-{right_inset}:{char_y}:shortest=0[vout]")
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

    if clip_volume is not None and has_clip:
        cv = float(clip_volume)
        if tts_path:
            audio_filter = (
                f"[{clip_idx}:a]volume={cv:.3f}[clipv];"
                f"[{tts_idx}:a]volume={vg:.3f}[voice];"
                f"[{bgm_idx}:a]afade=t=out:st={fade_st}:d=5,volume={bv:.3f}[bgm];"
                f"[clipv][voice][bgm]amix=inputs=3:duration=longest:dropout_transition=3:normalize=0[mix];"
                f"[mix]alimiter=limit=0.89:level=disabled[aout]"
            )
        else:
            audio_filter = (
                f"[{clip_idx}:a]volume={cv:.3f}[clipv];"
                f"[{bgm_idx}:a]afade=t=out:st={fade_st}:d=5,volume={bv_alone:.3f}[bgm];"
                f"[clipv][bgm]amix=inputs=2:duration=longest:dropout_transition=3:normalize=0[mix];"
                f"[mix]alimiter=limit=0.89:level=disabled[aout]"
            )
    elif tts_path:
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
