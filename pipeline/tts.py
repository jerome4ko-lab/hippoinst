import base64
import subprocess
from pathlib import Path

import requests
from elevenlabs.client import ElevenLabs

import config


# ── Public API ────────────────────────────────────────────────────────────────

def generate_tts(
    narration: list[str],
    *,
    provider: str = None,
    voice_id: str = None,
) -> tuple[Path, float, list[dict]]:
    """
    Generate TTS with word-level timing.
    Returns (mp3_path, duration_seconds, words)
        words = [{"word": str, "start": float, "end": float}, ...]

    provider="elevenlabs" → real char-level alignment from API
    provider="typecast"   → fake word timings distributed by char ratio
    """
    p = (provider or config.TTS_PROVIDER or "elevenlabs").lower()
    if p == "typecast":
        return _generate_typecast(narration, voice_id)
    return _generate_elevenlabs(narration, voice_id)


def synthesize_preview(
    text: str,
    *,
    provider: str,
    voice_id: str = None,
) -> Path:
    """Render a short preview MP3 to temp/preview.mp3 and return the path."""
    p = (provider or "elevenlabs").lower()
    out = config.TEMP_DIR / "preview.mp3"
    config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    if p == "typecast":
        _typecast_to_mp3(text, voice_id or config.TYPECAST_VOICE_ID, out)
    else:
        _elevenlabs_to_mp3(text, voice_id or config.ELEVENLABS_VOICE_ID, out)
    return out


# ── ElevenLabs ────────────────────────────────────────────────────────────────

def _generate_elevenlabs(narration: list[str], voice_id: str | None):
    text   = "\n".join(narration)
    vid    = voice_id or config.ELEVENLABS_VOICE_ID
    client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)

    from elevenlabs.types import VoiceSettings
    response = client.text_to_speech.convert_with_timestamps(
        voice_id=vid,
        text=text,
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        voice_settings=VoiceSettings(
            stability=0.5,
            similarity_boost=0.75,
            speed=config.TTS_SPEED,
        ),
    )

    tts_path = config.TEMP_DIR / "tts.mp3"
    tts_path.write_bytes(base64.b64decode(response.audio_base_64))

    words    = _parse_words(response.alignment)
    duration = _get_duration(tts_path)
    return tts_path, duration, words


def _elevenlabs_to_mp3(text: str, voice_id: str, out_path: Path) -> None:
    client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)
    from elevenlabs.types import VoiceSettings
    audio_iter = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        voice_settings=VoiceSettings(
            stability=0.5,
            similarity_boost=0.75,
            speed=config.TTS_SPEED,
        ),
    )
    with open(out_path, "wb") as f:
        for chunk in audio_iter:
            if chunk:
                f.write(chunk)


def _parse_words(alignment) -> list[dict]:
    """Convert char-level alignment to word-level timing."""
    words, current, word_start = [], "", None

    for char, start, end in zip(
        alignment.characters,
        alignment.character_start_times_seconds,
        alignment.character_end_times_seconds,
    ):
        if char in (" ", "\n"):
            if current.strip():
                words.append({"word": current.strip(), "start": word_start, "end": end})
            current = ""
            word_start = None
        else:
            if not current:
                word_start = start
            current += char

    if current.strip():
        words.append({
            "word":  current.strip(),
            "start": word_start,
            "end":   alignment.character_end_times_seconds[-1],
        })
    return words


# ── Typecast ──────────────────────────────────────────────────────────────────

_TYPECAST_URL = "https://api.typecast.ai/v1/text-to-speech"


def _generate_typecast(narration: list[str], voice_id: str | None):
    text = "\n".join(narration)
    vid  = voice_id or config.TYPECAST_VOICE_ID

    tts_path = config.TEMP_DIR / "tts.mp3"
    _typecast_to_mp3(text, vid, tts_path)

    duration = _get_duration(tts_path)
    words    = _fake_words_from_text(text, duration)
    return tts_path, duration, words


def _typecast_to_mp3(text: str, voice_id: str, out_path: Path) -> None:
    if not config.TYPECAST_API_KEY:
        raise RuntimeError("TYPECAST_API_KEY 환경변수가 비어있어요")

    payload = {
        "voice_id": voice_id,
        "text":     text,
        "model":    config.TYPECAST_MODEL,
        "language": "kor",
        "output": {
            "audio_format": "mp3",
            "audio_tempo":  float(config.TTS_SPEED),
        },
    }
    res = requests.post(
        _TYPECAST_URL,
        headers={
            "X-API-KEY":    config.TYPECAST_API_KEY,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    if res.status_code != 200:
        raise RuntimeError(
            f"Typecast TTS 실패 ({res.status_code}): {res.text[:300]}"
        )
    out_path.write_bytes(res.content)


def _fake_words_from_text(text: str, duration: float) -> list[dict]:
    """word-level alignment이 없을 때, 글자 비율로 시간을 분배해 word timing을 흉내."""
    tokens: list[str] = []
    for line in text.split("\n"):
        for w in line.strip().split():
            if w:
                tokens.append(w)
    if not tokens:
        return []

    total_chars = sum(len(w) for w in tokens) or 1
    t, out = 0.0, []
    for w in tokens:
        share = len(w) / total_chars
        end   = min(t + share * duration, duration)
        out.append({"word": w, "start": t, "end": end})
        t = end
    return out


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1",
         str(path)],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())
