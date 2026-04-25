from pathlib import Path
import config

# ASS color constants (BGR format: &H00BBGGRR&)
_WHITE  = "&H00FFFFFF&"
_YELLOW = "&H0040C0F0&"   # #f0c040 in RGB → BGR = 40C0F0


def generate_word_highlight_ass(words: list[dict], duration: float) -> Path:
    """
    TikTok-style word highlight: phrase displayed, current word in yellow.
    Subtitles positioned on the video clip area (not below).
    """
    # Bottom of clip zone (y=480~1440). Put text at y≈1370.
    margin_v = config.VIDEO_HEIGHT - (config.CLIP_Y + config.CLIP_H - 70)  # ~550

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

            # Build styled phrase: highlighted word in yellow+bold, others in white
            parts = []
            for j, w in enumerate(phrase):
                if j == idx:
                    parts.append(
                        f"{{\\c{_YELLOW}\\b1}}{w['word']}{{\\b0\\c{_WHITE}}}"
                    )
                else:
                    parts.append(f"{{\\c{_WHITE}}}{w['word']}")
            text = " ".join(parts)

            lines.append(f"Dialogue: 0,{start},{end},WordHL,,0,0,0,,{text}")

    ass_path = config.TEMP_DIR / "subtitles.ass"
    ass_path.write_text("\n".join(lines), encoding="utf-8-sig")
    return ass_path


def narration_to_subtitles(narration: list[str], duration: float = 45) -> list[dict]:
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


def generate_ass(subtitles: list, duration: float = 45) -> Path:
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
    return f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: {config.VIDEO_WIDTH}
PlayResY: {config.VIDEO_HEIGHT}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: WordHL,{font},{size},&H00FFFFFF,&H000000FF,&H00000000,&HC8000000,0,0,0,0,100,100,0,0,1,3,1,2,30,30,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""


def _to_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"
