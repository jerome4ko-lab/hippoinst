import base64
import re
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
    words    = _fake_words_from_text(text, duration, audio_path=tts_path)
    return tts_path, duration, words


def _typecast_to_mp3(text: str, voice_id: str, out_path: Path) -> None:
    if not config.TYPECAST_API_KEY:
        raise RuntimeError("TYPECAST_API_KEY 환경변수가 비어있어요")

    # 설정된 모델로 1차 시도, voice가 v30 미지원이면 v21로 폴백
    last_error: tuple[int, str] | None = None
    for model in _model_candidates(config.TYPECAST_MODEL):
        payload = {
            "voice_id": voice_id,
            "text":     text,
            "model":    model,
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
        if res.status_code == 200:
            out_path.write_bytes(res.content)
            return
        last_error = (res.status_code, res.text)
        # VOICE_MODEL_NOT_SUPPORTED 인 경우만 다음 후보로 진행
        if "VOICE_MODEL_NOT_SUPPORTED" not in res.text:
            break

    code, body = last_error or (0, "(no response)")
    raise RuntimeError(f"Typecast TTS 실패 ({code}): {body[:300]}")


def _model_candidates(primary: str) -> list[str]:
    """1차로 설정된 모델, 그 다음 안 들어가면 다른 버전으로 폴백."""
    fallback = "ssfm-v21" if primary != "ssfm-v21" else "ssfm-v30"
    return [primary, fallback]


def _fake_words_from_text(
    text: str,
    duration: float,
    audio_path: Path | None = None,
) -> list[dict]:
    """Word-level alignment이 없을 때 추정.

    가능하면 silencedetect로 문장 경계를 잡아 anchor로 쓴다 (Typecast가 문장 사이
    pause를 길게 두는 특성을 활용해 자막 drift 최소화).
    문장 내부에서는 글자 수 비율로 word를 분배.
    """
    sentences = _split_sentences(text)
    if not sentences:
        return []

    # 1) 글자 비율로 예상 경계 위치 → 근처 silence에 snap
    n = len(sentences)
    char_total = sum(_visible_len(s) for s in sentences) or 1
    expected: list[float] = []
    cum = 0
    for s in sentences[:-1]:
        cum += _visible_len(s)
        expected.append(cum / char_total * duration)

    silences = _detect_silences(audio_path) if audio_path else []
    snapped: list[float] = []
    used: set[int] = set()
    if n >= 2 and silences:
        # 한 silence는 한 경계에만 매핑되도록 used 추적
        for exp in expected:
            best_idx = -1
            best_score = float("inf")
            for idx, (s, e) in enumerate(silences):
                if idx in used:
                    continue
                mid = (s + e) / 2
                dist = abs(mid - exp)
                if dist > 3.0:        # 3초 이상 떨어진 silence는 후보 제외
                    continue
                # 길수록 가중치 ↑ (sentence 경계는 보통 더 김)
                score = dist - (e - s) * 0.6
                if score < best_score:
                    best_score = score
                    best_idx   = idx
            if best_idx >= 0:
                s, e = silences[best_idx]
                snapped.append((s + e) / 2)
                used.add(best_idx)
            else:
                # 매핑 실패 → 예상 위치 그대로 사용 (drift는 있지만 끝까지 안정)
                snapped.append(exp)

    boundaries: list[float] = [0.0]
    if len(snapped) == n - 1:
        boundaries.extend(snapped)
    else:
        # silence 매핑 실패 → 글자 비율 fallback
        boundaries.extend(expected)
    boundaries.append(duration)
    # 단조 증가 보장
    for i in range(1, len(boundaries)):
        if boundaries[i] < boundaries[i - 1]:
            boundaries[i] = boundaries[i - 1]

    # 2) 각 문장 내부에서 word를 글자 비율로 분배
    out: list[dict] = []
    for i, sent in enumerate(sentences):
        s_start = boundaries[i]
        s_end   = boundaries[i + 1]
        s_dur   = max(0.0, s_end - s_start)

        words = sent.split()
        if not words:
            continue
        sent_chars = sum(len(w) for w in words) or 1
        t = s_start
        for w in words:
            share = len(w) / sent_chars
            w_end = t + share * s_dur
            out.append({"word": w, "start": t, "end": w_end})
            t = w_end
    return out


def _split_sentences(text: str) -> list[str]:
    """종결 부호(.!?)로 문장 단위 분리. 줄바꿈은 단순 공백 취급.

    Typecast는 ``\n``에 대해 pause를 짧게/안 두는 경우가 많아 문장 경계로
    삼지 않는 편이 silencedetect anchor 매핑과 잘 맞는다.
    """
    flat = re.sub(r'\s+', ' ', text).strip()
    if not flat:
        return []
    parts = re.split(r'(?<=[.!?])\s+', flat)
    return [p.strip() for p in parts if p.strip()]


def _visible_len(s: str) -> int:
    return len(re.sub(r'\s+', '', s))


def _detect_silences(
    audio_path: Path | None,
    noise_db: float = -30,
    min_dur: float = 0.2,
) -> list[tuple[float, float]]:
    """ffmpeg silencedetect → [(silence_start, silence_end), ...]."""
    if audio_path is None or not Path(audio_path).exists():
        return []
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(audio_path),
             "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
             "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            timeout=30,
        )
    except Exception:
        return []

    starts = re.findall(r"silence_start:\s*([\d.]+)", result.stderr)
    ends   = re.findall(r"silence_end:\s*([\d.]+)", result.stderr)
    return [(float(s), float(e)) for s, e in zip(starts, ends)]


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
