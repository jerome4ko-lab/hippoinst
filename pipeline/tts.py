import base64
import subprocess
from pathlib import Path
from elevenlabs.client import ElevenLabs
import config


def generate_tts(narration: list[str], voice_id: str = None) -> tuple[Path, float, list[dict]]:
    """
    Generate TTS with word-level timestamps.
    Returns (mp3_path, duration_seconds, words)
    where words = [{"word": str, "start": float, "end": float}, ...]
    """
    text     = "\n".join(narration)
    vid      = voice_id or config.ELEVENLABS_VOICE_ID
    client   = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)

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


def _parse_words(alignment) -> list[dict]:
    """Convert char-level alignment to word-level timing (times already in seconds)."""
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


def _get_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1",
         str(path)],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())
