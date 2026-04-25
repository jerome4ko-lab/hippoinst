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

TYPECAST_API_KEY  = os.getenv("TYPECAST_API_KEY", "")
TYPECAST_VOICE_ID = os.getenv("TYPECAST_VOICE_ID", "tc_672c5f5ce59fac2a48faeaee")
TYPECAST_MODEL    = os.getenv("TYPECAST_MODEL", "ssfm-v21")

TTS_PROVIDER = os.getenv("TTS_PROVIDER", "elevenlabs")  # elevenlabs | typecast

KLIPY_API_KEY     = os.getenv("KLIPY_API_KEY", "")
KLIPY_CUSTOMER_ID = os.getenv("KLIPY_CUSTOMER_ID", "hippoinst")

VIDEO_WIDTH    = 1080
VIDEO_HEIGHT   = 1920
VIDEO_DURATION = 55

# Layout zones (y offset, height) in pixels
BANNER_Y = 0;    BANNER_H = 288   # 15%
TITLE_Y  = 288;  TITLE_H  = 192   # 10%
CLIP_Y   = 480;  CLIP_H   = 960   # 50%
SUB_Y    = 1440; SUB_H    = 288   # 15%
HASH_Y   = 1728; HASH_H   = 192   # 10%

# Fonts (bundled in assets — works on Windows & Linux)
FONT_BOLD    = str(ASSETS_DIR / "font_bold.ttf")
FONT_REGULAR = str(ASSETS_DIR / "font_regular.ttf")

SUBTITLE_FONT      = "Gmarket Sans TTF"  # ASS face name
SUBTITLE_FONT_SIZE = 58
SUBTITLE_PHRASES   = 4

TTS_SPEED = 1.2   # 1.0 = 기본, 1.2 = 20% 빠르게

# 음성/BGM 믹스 — 측정 기준: ElevenLabs ~-22 LUFS, Typecast ~-16 LUFS
TTS_VOICE_GAIN = {
    "elevenlabs": 4.0,   # 원본이 약간 작으므로 4배 부스트
    "typecast":   2.0,   # 원본이 6dB 더 커서 절반만
}
BGM_VOLUME           = 0.20   # 기존 0.08 → +8dB
BGM_VOLUME_NO_VOICE  = 0.30   # TTS 없을 때(이론상 사용 X)

COLORS = {
    "bg":        (14, 14, 14),
    "banner_bg": (26, 26, 46),
    "accent":    (240, 192, 64),
    "text":      (255, 255, 255),
    "hashtag":   (123, 104, 204),
    "brand":     (201, 184, 232),
}
