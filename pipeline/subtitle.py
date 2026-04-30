import re
from pathlib import Path
import config

# ASS color constants (BGR format: &H00BBGGRR&)
_WHITE  = "&H00FFFFFF&"
_YELLOW = "&H0040C0F0&"   # #f0c040 in RGB → BGR = 40C0F0

# UI에서 고를 수 있는 자막 강조 색 시안 (#RRGGBB)
SUBTITLE_COLOR_PRESETS = {
    "white":      "#FFFFFF",
    "gold":       "#FFD700",
    "mint":       "#00FFC8",
}
DEFAULT_SUBTITLE_COLOR = "#FFFFFF"


def hex_to_ass_color(hex_str: str | None) -> str:
    """`#RRGGBB` → ASS `&H00BBGGRR&`. 잘못된 입력이면 기본 흰색."""
    if not hex_str:
        return _WHITE
    s = hex_str.strip().lstrip("#")
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        return _WHITE
    r, g, b = s[0:2], s[2:4], s[4:6]
    return f"&H00{b}{g}{r}&".upper()


def generate_chunk_ass(
    chunks: list[str],
    words: list[dict],
    duration: float = 55,
    *,
    highlight_color: str | None = None,
) -> Path:
    """Render semantic-chunk subtitles aligned to TTS word timings.
    Each chunk is shown as one screen for its mapped duration (no per-word highlight).

    `highlight_color`는 `#RRGGBB`. None이면 디폴트 흰색.
    """
    margin_v = config.VIDEO_HEIGHT - (config.SUB_Y + config.SUB_H // 2)
    header   = _ass_header(config.SUBTITLE_FONT, config.SUBTITLE_FONT_SIZE, margin_v)
    lines    = [header]

    accent = hex_to_ass_color(highlight_color)

    aligned = align_chunks_to_words(chunks, words)
    if not aligned:
        return generate_word_highlight_ass(words, duration, highlight_color=highlight_color)

    for i, chunk in enumerate(aligned):
        start = _to_ass_time(chunk["start"])
        end_t = aligned[i + 1]["start"] if i < len(aligned) - 1 else chunk["end"]
        end   = _to_ass_time(end_t)
        text  = f"{{\\c{accent}\\b1}}{chunk['text']}{{\\b0\\c{_WHITE}}}"
        lines.append(f"Dialogue: 0,{start},{end},WordHL,,0,0,0,,{text}")

    ass_path = config.TEMP_DIR / "subtitles.ass"
    ass_path.write_text("\n".join(lines), encoding="utf-8-sig")
    return ass_path


def align_chunks_to_words(chunks: list[str], words: list[dict]) -> list[dict]:
    """Map chunk texts onto TTS word-level timings via character matching."""
    if not words or not chunks:
        return []

    char_timings: list[tuple[str, float, float]] = []
    for w in words:
        if not w.get("word"):
            continue
        for c in w["word"]:
            nc = _norm(c)
            if nc:
                char_timings.append((nc, w["start"], w["end"]))
    full_text = "".join(c for c, _, _ in char_timings)

    aligned, cursor = [], 0
    for chunk_text in chunks:
        target = _norm(chunk_text)
        if not target:
            continue
        idx = full_text.find(target, cursor)
        if idx < 0:
            idx = full_text.find(target)
            if idx < 0:
                continue
        end_idx = min(idx + len(target) - 1, len(char_timings) - 1)
        aligned.append({
            "text":  chunk_text,
            "start": char_timings[idx][1],
            "end":   char_timings[end_idx][2],
        })
        cursor = end_idx + 1
    return aligned


def chunk_narration(narration: list[str], min_chars: int = 6, max_chars: int = 14) -> list[str]:
    """Rule-based fallback: split narration into 6~14 char chunks at word boundaries.

    Strips periods entirely (subtitles are screen text — periods look messy).
    """
    def _clean(s: str) -> str:
        return s.replace(".", "").strip()

    chunks: list[str] = []
    for raw_line in narration:
        line = raw_line.strip()
        if not line:
            continue
        words = line.split()
        buf = ""
        for w in words:
            candidate = f"{buf} {w}".strip() if buf else w
            if _visible_len(candidate) > max_chars and buf:
                chunks.append(_clean(buf))
                buf = w
            else:
                buf = candidate
        if buf:
            if _visible_len(buf) < min_chars and chunks:
                chunks[-1] = _clean(f"{chunks[-1]} {buf}")
            else:
                chunks.append(_clean(buf))
    return [c for c in chunks if c]


def _visible_len(s: str) -> int:
    return len(re.sub(r'\s+', '', s))


def _norm(s: str) -> str:
    return re.sub(r'[\s.,!?…·\-—~`\'"()\[\]]+', '', s)


def generate_word_highlight_ass(
    words: list[dict],
    duration: float,
    *,
    highlight_color: str | None = None,
) -> Path:
    """
    TikTok-style word highlight: phrase displayed, current word colored.
    Subtitles positioned on the video clip area (not below).
    """
    # Bottom of clip zone (y=480~1440). Put text at y≈1370.
    margin_v = config.VIDEO_HEIGHT - (config.SUB_Y + config.SUB_H // 2)  # ~550

    accent = hex_to_ass_color(highlight_color)

    header = _ass_header(config.SUBTITLE_FONT, config.SUBTITLE_FONT_SIZE, margin_v)
    lines  = [header]

    phrase_size = config.SUBTITLE_PHRASES
    phrases = [words[i:i+phrase_size] for i in range(0, len(words), phrase_size)]

    for phrase in phrases:
        for idx, current_word in enumerate(phrase):
            start = _to_ass_time(current_word["start"])
            # Hold until next word starts (or phrase ends)
            if idx < len(phrase) - 1:
                end = _to_ass_time(phrase[idx + 1]["start"])
            else:
                end = _to_ass_time(current_word["end"])

            # Build styled phrase: highlighted word in accent+bold, others in white
            parts = []
            for j, w in enumerate(phrase):
                if j == idx:
                    parts.append(
                        f"{{\\c{accent}\\b1}}{w['word']}{{\\b0\\c{_WHITE}}}"
                    )
                else:
                    parts.append(f"{{\\c{_WHITE}}}{w['word']}")
            text = " ".join(parts)

            lines.append(f"Dialogue: 0,{start},{end},WordHL,,0,0,0,,{text}")

    ass_path = config.TEMP_DIR / "subtitles.ass"
    ass_path.write_text("\n".join(lines), encoding="utf-8-sig")
    return ass_path


def narration_to_subtitles(narration: list[str], duration: float = 55) -> list[dict]:
    """Distribute narration lines evenly, weighted by char count."""
    if not narration:
        return []
    total_chars = sum(len(line) for line in narration)
    subtitles, t = [], 0.0
    for line in narration:
        share = len(line) / total_chars
        end   = min(t + share * duration, duration)
        subtitles.append({"text": line, "start": round(t, 2), "end": round(end, 2)})
        t = end
    return subtitles


def generate_ass(subtitles: list, duration: float = 55) -> Path:
    """Fallback: simple subtitle file in the subtitle zone."""
    zone_center_y = config.SUB_Y + config.SUB_H // 2
    margin_v      = config.VIDEO_HEIGHT - zone_center_y

    header = _ass_header("Malgun Gothic", 44, margin_v)
    lines  = [header]
    for sub in subtitles:
        start = _to_ass_time(sub["start"])
        end   = _to_ass_time(sub["end"])
        text  = sub["text"].replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{start},{end},WordHL,,0,0,0,,{text}")

    ass_path = config.TEMP_DIR / "subtitles.ass"
    ass_path.write_text("\n".join(lines), encoding="utf-8-sig")
    return ass_path


# ── helpers ──────────────────────────────────────────────────────────────────

def _ass_header(font: str, size: int, margin_v: int) -> str:
    margin_x = int(getattr(config, "SUBTITLE_X_OFFSET", 0))
    margin_l = max(0, 30 + margin_x)
    margin_r = max(0, 30 - margin_x)
    return f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: {config.VIDEO_WIDTH}
PlayResY: {config.VIDEO_HEIGHT}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: WordHL,{font},{size},&H00FFFFFF,&H000000FF,&H00000000,&HC8000000,0,0,0,0,100,100,0,0,1,3,1,2,{margin_l},{margin_r},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""


def _to_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"
