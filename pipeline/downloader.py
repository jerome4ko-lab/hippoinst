import subprocess
from pathlib import Path
import config


def download_clip(url: str, start: str = "00:00:00", duration: int = 55) -> Path:
    raw_path     = config.TEMP_DIR / "raw_clip.mp4"
    trimmed_path = config.TEMP_DIR / "clip.mp4"

    # 이전 실행물 제거 — 같은 경로면 yt-dlp가 다운로드를 건너뛴다
    raw_path.unlink(missing_ok=True)
    trimmed_path.unlink(missing_ok=True)

    # Download best video-only mp4 (audio stripped later anyway)
    _run([
        "yt-dlp",
        "--no-continue",
        "--force-overwrites",
        "-f", "bestvideo[ext=mp4]/bestvideo",
        "-o", str(raw_path),
        url,
    ])

    # Trim to [start, start+duration] and strip audio
    _run([
        "ffmpeg", "-y",
        "-ss", start,
        "-i", str(raw_path),
        "-t", str(duration),
        "-an",
        "-c:v", "copy",
        str(trimmed_path),
    ])

    return trimmed_path


def _run(cmd: list) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(str(c) for c in cmd)}\n{result.stderr}")
