import base64
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import requests
from elevenlabs.client import ElevenLabs

import config


# ── Cache (provider, voice, narration) → mp3 + duration + words ────────────────

_TTS_CACHE_DIR = config.TEMP_DIR / "tts_cache"
_KOREAN_CPS    = 6.5    # 한국어 평균 발화 속도 (TTS_SPEED=1.0 기준 chars/sec)
_LINE_PAUSE_S  = 0.35   # 줄바꿈마다 추가되는 평균 pause
_TTS_CACHE_ID_RE = re.compile(r"^[0-9a-f]{16}$")


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

    동일 (provider, voice_id, narration, TTS_SPEED, TYPECAST_MODEL) 조합은
    `temp/tts_cache/`에 캐시되어 즉시 반환됨 (API 호출 skip).
    """
    p   = (provider or config.TTS_PROVIDER or "elevenlabs").lower()
    vid = _voice_id_or_default(p, voice_id)

    key = _cache_key(narration, p, vid)
    hit = _cache_get(key)
    if hit is not None:
        return hit

    if p == "typecast":
        result = _generate_typecast(narration, vid)
    else:
        result = _generate_elevenlabs(narration, vid)

    try:
        _cache_put(key, result[0], result[1], result[2])
        _cache_evict()
    except Exception as exc:
        # 캐시 실패는 본 합성에 영향 없게 무시
        print(f"[tts] cache put failed (non-fatal): {exc}", flush=True)
    return result


def estimate_tts_duration(narration) -> float:
    """글자수 기반 즉석 추정. API 호출 없음. 한국어 기준.

    공식: visible_chars / (KOREAN_CPS * TTS_SPEED) + line_count * LINE_PAUSE_S
    """
    if isinstance(narration, str):
        lines = [ln for ln in narration.splitlines() if ln.strip()]
    else:
        lines = [str(ln) for ln in (narration or []) if str(ln).strip()]
    if not lines:
        return 0.0
    chars = sum(len(re.sub(r"\s+", "", ln)) for ln in lines)
    speed = max(0.1, float(config.TTS_SPEED or 1.0))
    return chars / (_KOREAN_CPS * speed) + len(lines) * _LINE_PAUSE_S


def lookup_cached_tts_duration(
    narration,
    *,
    provider: str = None,
    voice_id: str = None,
) -> float | None:
    """캐시에 측정값이 있으면 반환, 없으면 None. API 호출 없음."""
    if isinstance(narration, str):
        lines = [ln for ln in narration.splitlines() if ln.strip()]
    else:
        lines = [str(ln).strip() for ln in (narration or []) if str(ln).strip()]
    if not lines:
        return None
    p   = (provider or config.TTS_PROVIDER or "elevenlabs").lower()
    vid = _voice_id_or_default(p, voice_id)
    key = _cache_key(lines, p, vid)
    hit = _cache_get(key)
    return hit[1] if hit is not None else None


def tts_cache_id(
    narration,
    *,
    provider: str = None,
    voice_id: str = None,
) -> str:
    """Return the deterministic cache id for a TTS request."""
    lines = _normalize_narration(narration)
    p = (provider or config.TTS_PROVIDER or "elevenlabs").lower()
    vid = _voice_id_or_default(p, voice_id)
    return _cache_key(lines, p, vid)


def tts_cache_audio_path(tts_id: str) -> Path | None:
    """Return the cached MP3 path for a confirmed TTS id, without copying it."""
    if not _TTS_CACHE_ID_RE.match(str(tts_id or "")):
        raise ValueError("잘못된 TTS 캐시 ID입니다")
    mp3, meta = _cache_paths(tts_id)
    if mp3.exists() and meta.exists():
        return mp3
    return None


def get_confirmed_tts(
    narration,
    confirmed_tts_id: str,
    *,
    provider: str = None,
    voice_id: str = None,
) -> tuple[Path, float, list[dict]]:
    """Load a previously confirmed TTS if it matches the current request."""
    expected = tts_cache_id(narration, provider=provider, voice_id=voice_id)
    if str(confirmed_tts_id or "") != expected:
        raise ValueError("확정 TTS가 현재 나레이션/목소리와 일치하지 않아요")
    hit = _cache_get(expected)
    if hit is None:
        raise FileNotFoundError("확정 TTS 캐시를 찾을 수 없어요. 다시 확정해주세요")
    return hit


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _voice_id_or_default(provider: str, voice_id: str | None) -> str:
    if voice_id:
        return voice_id
    if provider == "typecast":
        return config.TYPECAST_VOICE_ID
    return config.ELEVENLABS_VOICE_ID


def _voice_id_gain(voice_id: str | None) -> float:
    raw = getattr(config, "TTS_VOICE_ID_GAIN", {}).get(voice_id or "", 1.0)
    try:
        return max(0.01, float(raw))
    except (TypeError, ValueError):
        return 1.0


def _apply_voice_id_gain(audio_path: Path, voice_id: str | None) -> None:
    gain = _voice_id_gain(voice_id)
    if abs(gain - 1.0) < 0.001:
        return

    tmp_path = audio_path.with_name(f"{audio_path.stem}.gain{audio_path.suffix}")
    cmd = [
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-i", str(audio_path),
        "-af", f"volume={gain:.6f},alimiter=limit=0.89:level=disabled",
        "-c:a", "libmp3lame", "-b:a", "128k",
        str(tmp_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"TTS voice gain failed:\n{result.stderr[-1000:]}")
    tmp_path.replace(audio_path)


def _normalize_narration(narration) -> list[str]:
    if isinstance(narration, str):
        return [ln for ln in narration.splitlines() if ln.strip()]
    return [str(ln).strip() for ln in (narration or []) if str(ln).strip()]


def _cache_key(narration, provider: str, voice_id: str) -> str:
    lines = _normalize_narration(narration)
    payload = json.dumps([
        provider,
        voice_id or "",
        float(config.TTS_SPEED or 1.0),
        _voice_id_gain(voice_id),
        config.TYPECAST_MODEL if provider == "typecast" else "eleven_multilingual_v2",
        "\n".join(lines),
    ], ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _cache_paths(key: str) -> tuple[Path, Path]:
    return _TTS_CACHE_DIR / f"{key}.mp3", _TTS_CACHE_DIR / f"{key}.json"


def _cache_get(key: str) -> tuple[Path, float, list[dict]] | None:
    mp3, meta = _cache_paths(key)
    if not (mp3.exists() and meta.exists()):
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        duration = float(data["duration"])
        words = data.get("words") or []
        # 본 합성과 같은 위치(temp/tts.mp3)로 복사 — 캐시 원본 보존
        config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        out_path = config.TEMP_DIR / "tts.mp3"
        shutil.copyfile(mp3, out_path)
        # touch for LRU
        try:
            mp3.touch(exist_ok=True)
            meta.touch(exist_ok=True)
        except Exception:
            pass
        return out_path, duration, words
    except Exception:
        return None


def _cache_put(key: str, mp3: Path, duration: float, words: list[dict]) -> None:
    _TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_mp3, cache_meta = _cache_paths(key)
    tmp_mp3  = cache_mp3.with_suffix(".mp3.tmp")
    tmp_meta = cache_meta.with_suffix(".json.tmp")
    shutil.copyfile(mp3, tmp_mp3)
    tmp_meta.write_text(
        json.dumps({"duration": float(duration), "words": words}, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp_mp3.replace(cache_mp3)
    tmp_meta.replace(cache_meta)


def _cache_evict(max_entries: int = 30, max_age_days: int = 14) -> None:
    if not _TTS_CACHE_DIR.exists():
        return
    now = time.time()
    age_limit = max_age_days * 86400
    entries: list[tuple[float, Path, Path]] = []
    for mp3 in _TTS_CACHE_DIR.glob("*.mp3"):
        meta = mp3.with_suffix(".json")
        try:
            mtime = mp3.stat().st_mtime
        except OSError:
            continue
        # 너무 오래되면 즉시 삭제
        if now - mtime > age_limit:
            for p in (mp3, meta):
                try: p.unlink(missing_ok=True)
                except Exception: pass
            continue
        entries.append((mtime, mp3, meta))
    # LRU — 오래된 것부터 삭제
    if len(entries) > max_entries:
        entries.sort(key=lambda t: t[0])
        for _, mp3, meta in entries[:len(entries) - max_entries]:
            for p in (mp3, meta):
                try: p.unlink(missing_ok=True)
                except Exception: pass


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
    _apply_voice_id_gain(tts_path, vid)

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
    _apply_voice_id_gain(out_path, voice_id)


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
_TYPECAST_VOICES_URL = "https://api.typecast.ai/v1/voices"
_TYPECAST_RETRY_STATUS = {500, 502, 503, 504}
_TYPECAST_ATTEMPTS_PER_MODEL = 3
_TYPECAST_VOICE_MODELS: dict[str, list[str]] | None = None


class TypecastTTSError(RuntimeError):
    def __init__(self, status_code: int, body: str, *, fallback_allowed: bool):
        self.status_code = status_code
        self.body = body
        self.fallback_allowed = fallback_allowed
        super().__init__(f"Typecast TTS 실패 ({status_code}): {body[:300]}")


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

    # 설정된 모델로 1차 시도하되, Typecast /voices 기준 미지원 모델은 보내지 않는다.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    last_error: tuple[int, str, bool] | None = None
    for model in _model_candidates(config.TYPECAST_MODEL, voice_id):
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
        model_not_supported = False
        for attempt in range(1, _TYPECAST_ATTEMPTS_PER_MODEL + 1):
            try:
                res = requests.post(
                    _TYPECAST_URL,
                    headers={
                        "X-API-KEY":    config.TYPECAST_API_KEY,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=120,
                )
            except requests.RequestException as exc:
                last_error = (0, str(exc), True)
                if attempt < _TYPECAST_ATTEMPTS_PER_MODEL:
                    time.sleep(1.5 * attempt)
                    continue
                code, body, fallback_allowed = last_error
                raise TypecastTTSError(code, body, fallback_allowed=fallback_allowed)

            if res.status_code == 200:
                out_path.write_bytes(res.content)
                _apply_voice_id_gain(out_path, voice_id)
                return

            body = res.text or ""
            if "VOICE_MODEL_NOT_SUPPORTED" in body:
                last_error = (res.status_code, body, False)
                model_not_supported = True
                break

            transient = res.status_code in _TYPECAST_RETRY_STATUS
            last_error = (res.status_code, body, transient)
            if transient and attempt < _TYPECAST_ATTEMPTS_PER_MODEL:
                time.sleep(1.5 * attempt)
                continue
            code, body, fallback_allowed = last_error
            raise TypecastTTSError(code, body, fallback_allowed=fallback_allowed)

        if model_not_supported:
            continue

    code, body, fallback_allowed = last_error or (0, "(no response)", True)
    raise TypecastTTSError(code, body, fallback_allowed=fallback_allowed)


def _model_candidates(primary: str, voice_id: str | None = None) -> list[str]:
    """1차로 설정된 모델, 그 다음 안 들어가면 다른 버전으로 폴백."""
    primary = primary or "ssfm-v21"
    supported = _typecast_supported_models(voice_id)
    if supported:
        ordered = []
        if primary in supported:
            ordered.append(primary)
        ordered.extend(model for model in supported if model not in ordered)
        return ordered
    if primary == "ssfm-v30":
        return ["ssfm-v30", "ssfm-v21"]
    return ["ssfm-v21"]


def _typecast_supported_models(voice_id: str | None) -> list[str]:
    if not voice_id or not config.TYPECAST_API_KEY:
        return []
    global _TYPECAST_VOICE_MODELS
    if _TYPECAST_VOICE_MODELS is None:
        try:
            res = requests.get(
                _TYPECAST_VOICES_URL,
                headers={"X-API-KEY": config.TYPECAST_API_KEY},
                timeout=20,
            )
            res.raise_for_status()
            models: dict[str, list[str]] = {}
            for item in res.json():
                vid = item.get("voice_id")
                model = item.get("model")
                if vid and model:
                    models.setdefault(vid, [])
                    if model not in models[vid]:
                        models[vid].append(model)
            _TYPECAST_VOICE_MODELS = models
        except Exception as exc:
            print(f"[tts] Typecast voices lookup failed: {exc}", flush=True)
            _TYPECAST_VOICE_MODELS = {}
    return _TYPECAST_VOICE_MODELS.get(voice_id, [])


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
    """종결 부호(.!?)와 줄바꿈으로 문장 단위 분리.

    Typecast가 줄 경계에서 pause를 둘 때 silencedetect anchor가 더 잘 맞도록
    줄바꿈도 sentence boundary 후보로 유지한다.
    """
    normalized = re.sub(r'[ \t\r\f\v]+', ' ', text).strip()
    if not normalized:
        return []
    parts = re.split(r'(?<=[.!?])\s+|\n+', normalized)
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
