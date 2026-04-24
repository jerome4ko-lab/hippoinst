import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR    = Path(__file__).parent
ASSETS_DIR  = BASE_DIR / "assets"
OUTPUT_DIR  = BASE_DIR / "output"
TEMP_DIR    = BASE_DIR / "temp"

BANNER_PATH = ASSETS_DIR / "banner.png"

BGM_MAP = {
    "bgm_impact": ASSETS_DIR / "bgm_impact.mp3",
    "bgm_light":  ASSETS_DIR / "bgm_light.mp3",
    "bgm_future": ASSETS_DIR / "bgm_future.mp3",
}
BGM_FALLBACK = ASSETS_DIR / "bgm_light.mp3"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = "claude-sonnet-4-6"

ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "zgDzx5jLLCqEp6Fl7Kl7")

VIDEO_WIDTH    = 1080
VIDEO_HEIGHT   = 1920
VIDEO_DURATION = 45

# Layout zones (y offset, height) in pixels
BANNER_Y = 0;    BANNER_H = 288   # 15%
TITLE_Y  = 288;  TITLE_H  = 192   # 10%
CLIP_Y   = 480;  CLIP_H   = 960   # 50%
SUB_Y    = 1440; SUB_H    = 288   # 15%
HASH_Y   = 1728; HASH_H   = 192   # 10%

# Fonts (Windows / Linux auto-detect)
import platform as _platform
_LINUX = _platform.system() == "Linux"

FONT_BOLD    = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"    if _LINUX else "C:/Windows/Fonts/malgunbd.ttf"
FONT_REGULAR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc" if _LINUX else "C:/Windows/Fonts/malgun.ttf"

SUBTITLE_FONT      = "Noto Sans CJK KR" if _LINUX else "NanumSquareRoundEB"
SUBTITLE_FONT_SIZE = 58
SUBTITLE_PHRASES   = 4

COLORS = {
    "bg":        (14, 14, 14),
    "banner_bg": (26, 26, 46),
    "accent":    (240, 192, 64),
    "text":      (255, 255, 255),
    "hashtag":   (123, 104, 204),
    "brand":     (201, 184, 232),
}
