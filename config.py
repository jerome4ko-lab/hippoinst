import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR    = Path(__file__).parent
ASSETS_DIR  = BASE_DIR / "assets"
OUTPUT_DIR  = BASE_DIR / "output"
TEMP_DIR    = BASE_DIR / "temp"

BGM_DIR = ASSETS_DIR / "bgm"
BGM_MAP = {
    "bgm_impact": BGM_DIR / "bgm_impact.mp3",
    "bgm_light":  BGM_DIR / "bgm_light.mp3",
    "bgm_future": BGM_DIR / "bgm_future.mp3",
}
BGM_FALLBACK = BGM_DIR / "bgm_light.mp3"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = "claude-sonnet-4-6"

ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "zgDzx5jLLCqEp6Fl7Kl7")

TYPECAST_API_KEY  = os.getenv("TYPECAST_API_KEY", "")
TYPECAST_VOICE_ID = os.getenv("TYPECAST_VOICE_ID", "tc_62d66c3ef075c6ebd4114bd5")
TYPECAST_MODEL    = os.getenv("TYPECAST_MODEL", "ssfm-v21")

TTS_PROVIDER = os.getenv("TTS_PROVIDER", "typecast")  # elevenlabs | typecast

KLIPY_API_KEY     = os.getenv("KLIPY_API_KEY", "")
KLIPY_CUSTOMER_ID = os.getenv("KLIPY_CUSTOMER_ID", "hippoinst")

# YouTube Data API v3 — OAuth (Desktop) one-time 셋업 후 refresh_token 으로 access_token 갱신
YOUTUBE_CLIENT_ID     = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN", "")

VIDEO_WIDTH    = 1080
VIDEO_HEIGHT   = 1920
VIDEO_DURATION = 55

# 캐릭터 립싱크 오버레이 (TTS RMS 기반)
CHARACTER_ENABLED = True
CHARACTER_SIZE    = 219                     # 정사각형 px (ex-243에서 10% 축소)
CHARACTER_DIR     = ASSETS_DIR / "character"
CHARACTER_RIGHT_INSET = 176                 # 오른쪽에서 안쪽으로 들어가는 px (값↑ = 왼쪽 이동)
CHARACTER_BELOW_SUBTITLE_GAP = -109         # 자막 띠 아래 간격 px (값↑ = 아래 이동)

# 비디오 클립 페더링(테두리 부드럽게) — bg_laser 와의 블렌딩
CLIP_FEATHER_PX = 15

# Layout zones (y offset, height) in pixels
# y=0~170 (170px)은 상단 검정 여백 — YouTube 상단 UI(헤더/탭) 회피
PILL_Y   = 200;  PILL_H   = 80    # 노란 알약(부제) 슬롯
TITLE_Y  = 170;  TITLE_H  = 380   # 검정 타이틀 블록
CLIP_Y   = 530;  CLIP_H   = 810   # 클립 4:3 (1080×810)
SUB_Y    = 1360; SUB_H    = 200   # 자막 띠 (클립 아래)
# y=1560~1920 (360px)은 하단 검정 여백.
# 미리보기에선 YouTube 모바일 UI 가상 오버레이가 들어감.

# Fonts (bundled in assets — works on Windows & Linux)
FONT_BOLD    = str(ASSETS_DIR / "font_bold.ttf")
FONT_REGULAR = str(ASSETS_DIR / "font_regular.ttf")

SUBTITLE_FONT      = "Gmarket Sans TTF"  # ASS face name
SUBTITLE_FONT_SIZE = 58
SUBTITLE_PHRASES   = 4
SUBTITLE_X_OFFSET  = -23                 # px (음수 = 왼쪽 이동)

HOOK_ACCENT_COLOR_DEFAULT = "#F0C040"
HOOK_ACCENT_COLOR_PRESETS = {
    "gold":      HOOK_ACCENT_COLOR_DEFAULT,
    "mint":      "#00FFC8",
    "redorange": "#FF4500",
}

TTS_SPEED = 1.2   # 1.0 = 기본, 1.2 = 20% 빠르게

# 음성/BGM 믹스 — 측정 기준: ElevenLabs ~-22 LUFS, Typecast ~-16 LUFS
TTS_VOICE_GAIN = {
    "elevenlabs": 4.0,   # 원본이 약간 작으므로 4배 부스트
    "typecast":   2.0,   # Typecast는 원본이 큰 편이지만 BGM ducking 후 명료도 우선
}
BGM_VOLUME           = 0.22   # TTS가 있을 때: 음성이 묻히지 않게 낮게 유지
BGM_VOLUME_NO_VOICE  = 1.00   # TTS 없을 때

COLORS = {
    "bg":        (14, 14, 14),
    "banner_bg": (26, 26, 46),       # 클립 영역 그라디언트용 (그대로 유지)
    "accent":    (240, 192, 64),     # 노란 강조 / 알약 배경
    "text":      (255, 255, 255),    # 흰색 메인 타이틀
    "pill_text": (24, 18, 6),        # 알약 위 짙은 글씨
    "brand":     (201, 184, 232),
}
