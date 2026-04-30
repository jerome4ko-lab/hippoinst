"""Verify that get_confirmed_tts reuses the disk cache without re-synthesizing.

Runs from repo root:  python tools/verify_confirmed_tts.py
"""
from __future__ import annotations
import sys, time, hashlib, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from pipeline.tts import (
    generate_tts, get_confirmed_tts, tts_cache_id, tts_cache_audio_path,
)

NARRATION = ["하마는 의외로 빨라요.", "시속 30km까지 달릴 수 있어요."]
PROVIDER = "typecast"
VOICE = "tc_62d66c3ef075c6ebd4114bd5"


def _md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    expected_id = tts_cache_id(NARRATION, provider=PROVIDER, voice_id=VOICE)
    print(f"[probe] expected tts_id = {expected_id}")

    cached_mp3 = tts_cache_audio_path(expected_id)
    if not (cached_mp3 and cached_mp3.exists()):
        print("[probe] no cache yet — calling generate_tts() to seed it")
        path, dur, _ = generate_tts(NARRATION, provider=PROVIDER, voice_id=VOICE)
        cached_mp3 = tts_cache_audio_path(expected_id)

    assert cached_mp3 and cached_mp3.exists(), "cache mp3 must exist after seed"
    cache_md5 = _md5(cached_mp3)
    print(f"[probe] cache mp3   = {cached_mp3}  md5={cache_md5}  size={cached_mp3.stat().st_size}")

    out_path = config.TEMP_DIR / "tts.mp3"
    if out_path.exists():
        out_path.unlink()
    print(f"[probe] removed {out_path} (will be recreated by get_confirmed_tts)")

    t0 = time.perf_counter()
    path, dur, words = get_confirmed_tts(
        NARRATION, expected_id, provider=PROVIDER, voice_id=VOICE,
    )
    elapsed = time.perf_counter() - t0
    print(f"[probe] get_confirmed_tts() -> {path}  dur={dur:.2f}s  elapsed={elapsed*1000:.1f}ms")
    print(f"[probe] words[:3]={words[:3] if words else []!r}")

    assert path.exists(), "returned path must exist"
    out_md5 = _md5(path)
    print(f"[probe] copied  mp3 = {path}  md5={out_md5}  size={path.stat().st_size}")

    if out_md5 != cache_md5:
        print("[FAIL] md5 mismatch — copy ≠ cache (re-synthesis suspected)")
        return 1
    if elapsed > 0.5:
        print(f"[WARN] elapsed {elapsed:.2f}s is suspiciously long for a file copy")

    # Negative branch: wrong id
    print("\n[probe] negative: wrong tts_id should raise ValueError")
    try:
        get_confirmed_tts(NARRATION, "0000000000000000",
                          provider=PROVIDER, voice_id=VOICE)
        print("[FAIL] expected ValueError on mismatched id")
        return 1
    except ValueError as e:
        print(f"[ok] raised ValueError: {e}")

    # Negative branch: changed narration with same id
    print("\n[probe] negative: id stale vs different narration")
    try:
        get_confirmed_tts(["전혀 다른 문장입니다."], expected_id,
                          provider=PROVIDER, voice_id=VOICE)
        print("[FAIL] expected ValueError on stale id")
        return 1
    except ValueError as e:
        print(f"[ok] raised ValueError: {e}")

    print("\n[PASS] confirmed-TTS reuse verified - cache copy, no re-synthesis.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
