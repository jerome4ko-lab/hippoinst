"""TTS 립싱크 캐릭터 오버레이.

오디오의 RMS 볼륨을 일정 fps로 샘플링해서 4가지 입 모양 PNG를 골라
무음 webm(VP9 + alpha) 영상으로 출력한다. 출력 webm은 ffmpeg overlay로
최종 합성 영상에 입혀진다.

Note: H.264(libx264)는 yuva420p(alpha) 미지원이라 mp4가 아닌 webm 사용.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import math
import subprocess

import config

try:
    import librosa
    _LIBROSA_OK = True
except Exception:
    _LIBROSA_OK = False

try:
    import imageio.v2 as imageio
    _IMAGEIO_OK = True
except Exception:
    try:
        import imageio
        _IMAGEIO_OK = True
    except Exception:
        _IMAGEIO_OK = False

try:
    import numpy as np
    _NUMPY_OK = True
except Exception:
    _NUMPY_OK = False

from PIL import Image


MOUTH_FILES: dict[str, str] = {
    "closed": "mouth_closed.png",
    "small":  "mouth_small.png",
    "open":   "mouth_open.png",
    "wide":   "mouth_wide.png",
}

# 오디오별 음량이 천차만별 (TTS 엔진/normalize 정도에 따라 RMS max가 0.005~0.3까지 흔들림)
# 절대값으로 임계값 잡으면 조용한 음성은 전부 closed로 떨어지는 회귀가 발생.
# → 오디오의 RMS 분포 백분위로 4분할: 음성 활동 강도에 자동 적응.
#
# 구간:
#   closed: 0~30% (유성음 비활성·조용한 자음)
#   small : 30~55%
#   open  : 55~80%
#   wide  : 80~100% (강세 모음)
_RMS_PERCENTILES: list[tuple[float, str]] = [
    (30.0, "closed"),
    (55.0, "small"),
    (80.0, "open"),
    (100.0, "wide"),
]
# 추가 안전망 — 절대 silence threshold (이거보다 작은 RMS는 무조건 closed로 강제)
_SILENCE_RMS = 1e-4


def is_available() -> bool:
    """캐릭터 오버레이 활성화 가능 여부 — config 토글, 라이브러리, 에셋 모두 점검."""
    if not getattr(config, "CHARACTER_ENABLED", False):
        return False
    if not _NUMPY_OK:
        return False
    return all((config.CHARACTER_DIR / fn).exists() for fn in MOUTH_FILES.values())


def audio_to_mouth_frames(audio_path: Path, fps: int = 30) -> list[tuple[str, int]]:
    """오디오를 fps 단위로 RMS 샘플링해 입 상태 RLE [(state, frame_count), ...] 반환.

    동일 상태가 연속되면 묶어서 한 항목으로 합친다.
    """
    if not _NUMPY_OK:
        return []

    sr = 16000
    try:
        raw = subprocess.run(
            [
                "ffmpeg", "-v", "error",
                "-i", str(audio_path),
                "-ac", "1",
                "-ar", str(sr),
                "-f", "f32le",
                "pipe:1",
            ],
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout
    except Exception:
        if not _LIBROSA_OK:
            return []
        y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    else:
        y = np.frombuffer(raw, dtype=np.float32)

    if y.size == 0 or sr <= 0:
        return []

    hop = max(1, int(round(sr / fps)))
    frame_len = hop * 2
    frame_count = max(1, int(math.ceil(y.size / hop)))
    rms_vals: list[float] = []
    for i in range(frame_count):
        start = i * hop
        frame = y[start:start + frame_len]
        if frame.size == 0:
            rms_vals.append(0.0)
        else:
            rms_vals.append(float(np.sqrt(np.mean(np.square(frame)))))
    rms = np.asarray(rms_vals, dtype=np.float32)

    # 백분위 → 절대 임계값으로 변환 (이 오디오 한정)
    thresholds = [
        (float(np.percentile(rms, pct)), name) for pct, name in _RMS_PERCENTILES
    ]

    states: list[str] = []
    for v in rms:
        if v < _SILENCE_RMS:
            states.append("closed")
            continue
        for thr, name in thresholds:
            if v <= thr:
                states.append(name)
                break

    if not states:
        return []

    rle: list[tuple[str, int]] = []
    cur = states[0]
    cnt = 1
    for s in states[1:]:
        if s == cur:
            cnt += 1
        else:
            rle.append((cur, cnt))
            cur, cnt = s, 1
    rle.append((cur, cnt))
    return rle


_mouth_cache: dict[str, "Image.Image"] = {}


def _load_mouth(state: str, size: int) -> "Image.Image":
    """입 모양 PNG 로드(메모리 캐시) + size×size로 리샘플."""
    key = f"{state}_{size}"
    cached = _mouth_cache.get(key)
    if cached is not None:
        return cached
    src = Image.open(config.CHARACTER_DIR / MOUTH_FILES[state]).convert("RGBA")
    if src.size != (size, size):
        src = src.resize((size, size), Image.LANCZOS)
    _mouth_cache[key] = src
    return src


def render_character_frames(
    audio_path: Path,
    output_dir: Path,
    fps: int = 30,
) -> Optional[Path]:
    """오디오 → 입 모양 RLE → output_dir/character_%06d.png 시퀀스 저장.

    PNG 시퀀스를 쓰는 이유: ffmpeg libvpx-vp9의 yuva420p alpha 인코딩이
    빌드/플랫폼별로 불안정 (지정해도 yuv420p로 떨어짐). PNG는 RGBA를
    네이티브로 지원해 후속 ffmpeg overlay에서 알파가 정확히 살아남음.

    - 의존성/에셋/토글이 하나라도 빠지면 None.
    - 반환: output_dir (Path). 비어있으면 None.
    """
    if not is_available():
        return None

    rle = audio_to_mouth_frames(Path(audio_path), fps=fps)
    if not rle:
        return None

    import shutil
    output_dir = Path(output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    size = int(config.CHARACTER_SIZE)
    idx = 1
    for state, cnt in rle:
        mouth = _load_mouth(state, size)
        for _ in range(int(cnt)):
            mouth.save(output_dir / f"character_{idx:06d}.png")
            idx += 1
    return output_dir


# 하위 호환 — 기존 호출자가 webm/mp4 경로를 넘겨도 디렉토리로 변환
def render_character_video(
    audio_path: Path,
    output_path: Path,
    fps: int = 30,
) -> Optional[Path]:
    output_path = Path(output_path)
    output_dir = output_path.with_suffix("") if output_path.suffix else output_path
    return render_character_frames(audio_path, output_dir, fps=fps)
