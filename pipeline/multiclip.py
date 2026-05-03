"""멀티클립 편집 — 여러 유튜브 클립을 xfade로 이어붙여 하이라이트 릴 mp4 생성.

기존 파이프라인(스크립트→TTS→영상)과 분리된 흐름. 사용 케이스:
  - 클립 N개 (2~5) → 4:3 크롭 → 1080×810 → xfade 0.4s → 타이틀 블록 위에 오버레이
  - 클립 오디오 mute, BGM만
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import config


ALLOWED_TRANSITIONS = {
    "fade", "wipeleft", "wiperight", "dissolve", "slideleft", "zoomin",
}
TRANSITION_DUR = 0.4   # 초

PREVIEW_DIR = config.TEMP_DIR / "preview"
PREVIEW2_DIR = config.TEMP_DIR / "preview2"
DOWNLOAD_TIMEOUT_SEC = 90
THUMBNAIL_TIMEOUT_SEC = 20


def parse_time(s: str) -> float:
    """Parse mm:ss, hh:mm:ss, or seconds into seconds."""
    s = str(s or "").strip()
    if not s:
        return 0.0
    s = s.removesuffix("초").strip()
    if ":" in s:
        raw_parts = s.split(":")
        if any(not p.strip() for p in raw_parts):
            raise ValueError(f"시간 형식이 비어 있어요: {s!r}. 예: 00:30 또는 30")
        try:
            parts = [float(p) for p in raw_parts]
        except ValueError:
            raise ValueError(f"시간 형식을 확인해주세요: {s!r}. 예: 00:30 또는 30")
        if len(parts) == 2:
            m, sec = parts
            return m * 60 + sec
        if len(parts) == 3:
            h, m, sec = parts
            return h * 3600 + m * 60 + sec
        raise ValueError(f"Bad time format: {s}")
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"시간 형식을 확인해주세요: {s!r}. 예: 00:30 또는 30")


def normalize_media_url(url: str) -> str:
    """Return a clean HTTP(S) media URL or raise a user-facing error."""
    clean = str(url or "").strip()
    if not clean:
        raise ValueError("URL을 입력해주세요")

    lower = clean.lower()
    if lower.startswith(("youtube.com/", "youtu.be/", "www.youtube.com/", "www.youtu.be/")):
        clean = f"https://{clean}"

    parsed = urlparse(clean)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"URL 형식이 아니에요: {clean}")
    return clean


def clip_id(url: str, start: float, end: float) -> str:
    """동일 (url,start,end)는 같은 id → 캐시 hit."""
    key = f"{url.strip()}|{start:.3f}|{end:.3f}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def download_section(url: str, start: float, end: float, out_path: Path) -> Path:
    """yt-dlp --download-sections로 [start, end] 구간만 다운로드 → mp4.

    이미 out_path 존재 시 스킵 (캐시).
    """
    url = normalize_media_url(url)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    if end <= start:
        raise ValueError(f"end({end})는 start({start})보다 커야 합니다")

    section = f"*{start:.2f}-{end:.2f}"
    stem = out_path.stem
    parent = out_path.parent

    import datetime
    log_path = parent / f"{stem}_debug.log"
    def dbg(msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    dbg(f"start url={url[:60]} section={section}")

    # 1. 비디오 다운로드
    _run([
        "yt-dlp", "--no-continue", "--force-overwrites",
        "--socket-timeout", "15", "--retries", "2", "--fragment-retries", "2",
        "--download-sections", section,
        "-f", "bestvideo[ext=mp4][height<=1080]/bestvideo[ext=mp4]/bestvideo",
        "-o", str(parent / f"{stem}_v.%(ext)s"),
        url,
    ], timeout=DOWNLOAD_TIMEOUT_SEC)
    video_files = list(parent.glob(f"{stem}_v.*"))
    video_tmp = video_files[0] if video_files else None
    dbg(f"video_files={video_files} video_tmp={video_tmp}")

    # 2. 오디오 다운로드
    audio_tmp = None
    try:
        _run([
            "yt-dlp", "--no-continue", "--force-overwrites",
            "--socket-timeout", "15", "--retries", "2", "--fragment-retries", "2",
            "--download-sections", section,
            "-f", "bestaudio[ext=m4a]/bestaudio",
            "-o", str(parent / f"{stem}_a.%(ext)s"),
            url,
        ], timeout=DOWNLOAD_TIMEOUT_SEC)
        audio_files = list(parent.glob(f"{stem}_a.*"))
        audio_tmp = audio_files[0] if audio_files else None
        dbg(f"audio_files={audio_files} audio_tmp={audio_tmp}")
    except Exception as e:
        dbg(f"오디오 다운로드 실패: {e}")

    # 3. 병합 또는 비디오만 사용
    try:
        if video_tmp and audio_tmp:
            dbg("ffmpeg 병합 시작")
            _run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(video_tmp), "-i", str(audio_tmp),
                "-c:v", "copy", "-c:a", "aac",
                str(out_path),
            ])
            dbg("ffmpeg 병합 완료")
        elif video_tmp:
            dbg("오디오 없음 - 비디오만 사용")
            video_tmp.replace(out_path)
    finally:
        if video_tmp: video_tmp.unlink(missing_ok=True)
        if audio_tmp: audio_tmp.unlink(missing_ok=True)

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"yt-dlp 다운로드 실패: {url} {section}")
    return out_path


def _probe_stream(path: Path, selector: str) -> bool:
    path = Path(path)
    if not path.exists() or path.stat().st_size <= 0:
        return False
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", selector,
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def has_audio_stream(path: Path) -> bool:
    return _probe_stream(path, "a:0")


def has_video_stream(path: Path) -> bool:
    return _probe_stream(path, "v:0")


def download_section_with_audio(url: str, start: float, end: float, out_path: Path) -> Path:
    """Download a section for produce2 and require both video and audio streams."""
    url = normalize_media_url(url)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists() and out_path.stat().st_size > 0:
        if has_video_stream(out_path) and has_audio_stream(out_path):
            return out_path
        out_path.unlink(missing_ok=True)

    if end <= start:
        raise ValueError(f"end({end}) must be greater than start({start})")

    section = f"*{start:.2f}-{end:.2f}"
    stem = out_path.stem
    parent = out_path.parent
    video_pattern = parent / f"{stem}_v.%(ext)s"
    audio_pattern = parent / f"{stem}_a.%(ext)s"
    merged = parent / f"{stem}_merged.mp4"

    for old in list(parent.glob(f"{stem}_v.*")) + list(parent.glob(f"{stem}_a.*")):
        old.unlink(missing_ok=True)
    merged.unlink(missing_ok=True)

    _run([
        "yt-dlp", "--no-continue", "--force-overwrites",
        "--socket-timeout", "15", "--retries", "2", "--fragment-retries", "2",
        "--download-sections", section,
        "-f", "bestvideo[height<=1080]/bestvideo/best[height<=1080]/best",
        "-o", str(video_pattern),
        url,
    ], timeout=DOWNLOAD_TIMEOUT_SEC)
    video_files = list(parent.glob(f"{stem}_v.*"))
    video_tmp = video_files[0] if video_files else None
    if video_tmp is None:
        raise RuntimeError("produce2 preview download failed: no video stream was downloaded")

    try:
        _run([
            "yt-dlp", "--no-continue", "--force-overwrites",
            "--socket-timeout", "15", "--retries", "2", "--fragment-retries", "2",
            "--download-sections", section,
            "-f", "bestaudio[ext=m4a]/bestaudio/best",
            "-o", str(audio_pattern),
            url,
        ], timeout=DOWNLOAD_TIMEOUT_SEC)
        audio_files = list(parent.glob(f"{stem}_a.*"))
        audio_tmp = audio_files[0] if audio_files else None
        if audio_tmp is None:
            raise RuntimeError("produce2 preview download failed: no audio stream was downloaded")

        _run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(video_tmp), "-i", str(audio_tmp),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(merged),
        ], timeout=120)

        if not has_video_stream(merged) or not has_audio_stream(merged):
            raise RuntimeError("produce2 preview mux failed: output is missing video or audio")
        merged.replace(out_path)
    finally:
        for f in list(parent.glob(f"{stem}_v.*")) + list(parent.glob(f"{stem}_a.*")):
            f.unlink(missing_ok=True)
        merged.unlink(missing_ok=True)

    if not has_video_stream(out_path) or not has_audio_stream(out_path):
        out_path.unlink(missing_ok=True)
        raise RuntimeError("produce2 preview failed: cached clip is missing video or audio")
    return out_path


def extract_thumbnail(video_path: Path, out_path: Path) -> Path:
    """ffmpeg로 비디오 첫 부분 한 프레임을 jpg로 저장."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", "0",
        "-i", str(video_path),
        "-vf", "scale=iw:ih:out_range=full,format=yuvj420p",
        "-frames:v", "1",
        "-update", "1",
        "-q:v", "4",
        str(out_path),
    ], timeout=THUMBNAIL_TIMEOUT_SEC)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg 썸네일 추출 실패: {video_path}")
    return out_path


def prepare_preview(url: str, start: float, end: float) -> dict:
    """미리보기용: 구간 다운로드 + 썸네일 추출. 캐시 사용."""
    url = normalize_media_url(url)
    cid = clip_id(url, start, end)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    video = PREVIEW_DIR / f"{cid}.mp4"
    thumb = PREVIEW_DIR / f"{cid}.jpg"

    download_section(url, start, end, video)
    if not thumb.exists() or thumb.stat().st_size == 0:
        try:
            extract_thumbnail(video, thumb)
        except Exception as e:
            # 썸네일은 UI 보조용이다. 영상 다운로드가 성공했으면 미리보기/합성은 계속 진행한다.
            print(f"[preview] thumbnail skip - {e}", flush=True)
            thumb = None

    return {
        "clip_id":  cid,
        "duration": round(end - start, 2),
        "video":    video,
        "thumb":    thumb,
    }


def prepare_preview2(url: str, start: float, end: float) -> dict:
    """Produce2-only preview: download the selected section with original audio."""
    url = normalize_media_url(url)
    cid = clip_id(url, start, end)
    PREVIEW2_DIR.mkdir(parents=True, exist_ok=True)
    video = PREVIEW2_DIR / f"{cid}.mp4"
    thumb = PREVIEW2_DIR / f"{cid}.jpg"

    download_section_with_audio(url, start, end, video)
    if not thumb.exists() or thumb.stat().st_size == 0:
        try:
            extract_thumbnail(video, thumb)
        except Exception as e:
            print(f"[preview2] thumbnail skip - {e}", flush=True)
            thumb = None

    return {
        "clip_id": cid,
        "duration": round(end - start, 2),
        "video": video,
        "thumb": thumb,
        "has_audio": has_audio_stream(video),
    }


def multiclip_duration(clips: list[dict]) -> float:
    """Return final timeline duration after fixed xfade overlaps."""
    if not clips:
        return 0.0
    total = sum(float(c["duration"]) for c in clips)
    total -= TRANSITION_DUR * max(0, len(clips) - 1)
    return max(0.0, total)


def compose_montage(
    clips: list[dict],
    transitions: list[str],
    output_path: Path,
) -> float:
    """Create a silent 1080x810 xfade montage for the final TTS pipeline.

    The returned file contains video only. The caller overlays it into the
    standard shorts template and mixes TTS/BGM separately.
    """
    n = len(clips)
    if n < 2:
        raise ValueError("멀티클립은 최소 2개 클립이 필요합니다")
    if len(transitions) != n - 1:
        raise ValueError(f"transitions 개수가 {n - 1}개여야 합니다 (현재 {len(transitions)})")

    sane_trans = [t if t in ALLOWED_TRANSITIONS else "fade" for t in transitions]

    clip_w = config.VIDEO_WIDTH
    clip_h = clip_w * 3 // 4

    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c["path"])]

    parts: list[str] = []
    for i in range(n):
        parts.append(
            f"[{i}:v]crop='min(iw\\,ih*4/3)':ih,"
            f"scale={clip_w}:{clip_h},setsar=1,fps=30,format=yuv420p[c{i}]"
        )

    cur = "c0"
    cum = float(clips[0]["duration"])
    for i in range(1, n):
        next_lbl = f"vmix{i}" if i < n - 1 else "vout"
        offset = max(0.0, cum - TRANSITION_DUR)
        parts.append(
            f"[{cur}][c{i}]xfade=transition={sane_trans[i-1]}:"
            f"duration={TRANSITION_DUR}:offset={offset:.2f}[{next_lbl}]"
        )
        cur = next_lbl
        cum += float(clips[i]["duration"]) - TRANSITION_DUR

    total_duration = multiclip_duration(clips)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(parts),
        "-map", "[vout]",
        "-t", f"{total_duration:.2f}",
        "-an",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg montage compose failed:\n{result.stderr[-3000:]}")
    return total_duration


def compose_multiclip(
    clips: list[dict],
    transitions: list[str],
    bgm_path: Path,
    bg_path: Path,
    output_path: Path,
    bgm_volume: Optional[float] = None,
) -> None:
    """ffmpeg xfade 체인으로 N개 클립 합성 → 타이틀 블록 위 오버레이 → BGM.

    clips: [{"path": Path, "duration": float}, ...]
    transitions: 길이 = len(clips)-1, ALLOWED_TRANSITIONS 내 값
    """
    n = len(clips)
    if n < 2:
        raise ValueError("멀티클립은 최소 2개 클립이 필요합니다")
    if len(transitions) != n - 1:
        raise ValueError(f"transitions 개수가 {n - 1}개여야 합니다 (현재 {len(transitions)})")

    # 트랜지션 sanitize
    sane_trans = [t if t in ALLOWED_TRANSITIONS else "fade" for t in transitions]

    clip_w  = config.VIDEO_WIDTH                                 # 1080
    clip_h  = clip_w * 3 // 4                                    # 810
    clip_y  = config.CLIP_Y + (config.CLIP_H - clip_h) // 2

    # ── 입력 ─────────────────────────────────────────────────────
    inputs: list[str] = ["-loop", "1", "-i", str(bg_path)]   # idx 0 = bg PNG
    for c in clips:
        inputs += ["-i", str(c["path"])]                     # idx 1..n
    inputs += ["-i", str(bgm_path)]                          # idx n+1 = BGM
    bgm_idx = n + 1

    # ── 비디오 필터 ─────────────────────────────────────────────
    parts: list[str] = []

    # 1) 각 클립을 4:3 크롭 + 1080×810으로 정규화
    for i in range(n):
        src = i + 1   # 0=bg, 1..n=clips
        parts.append(
            f"[{src}:v]crop='min(iw\\,ih*4/3)':ih,"
            f"scale={clip_w}:{clip_h},setsar=1,fps=30,format=yuv420p[c{i}]"
        )

    # 2) xfade 체인 — 누적 길이로 offset 계산
    cur = "c0"
    cum = float(clips[0]["duration"])
    for i in range(1, n):
        next_lbl = f"vmix{i}" if i < n - 1 else "vmerged"
        offset = cum - TRANSITION_DUR
        if offset < 0:
            offset = 0  # 너무 짧은 클립 안전장치
        parts.append(
            f"[{cur}][c{i}]xfade=transition={sane_trans[i-1]}:"
            f"duration={TRANSITION_DUR}:offset={offset:.2f}[{next_lbl}]"
        )
        cur = next_lbl
        cum += float(clips[i]["duration"]) - TRANSITION_DUR

    total_duration = cum  # 트랜지션 0.4s씩 겹쳤을 때의 최종 길이

    # 3) 타이틀 블록 위 오버레이
    parts.append(f"[0:v][{cur}]overlay=0:{clip_y}:format=auto[vout]")

    video_filter = ";".join(parts)

    # ── 오디오 ─────────────────────────────────────────────────
    bv = float(bgm_volume if bgm_volume is not None else config.BGM_VOLUME_NO_VOICE)
    fade_st = max(total_duration - 5, 0)
    audio_filter = (
        f"[{bgm_idx}:a]afade=t=out:st={fade_st:.2f}:d=5,volume={bv:.3f},"
        f"alimiter=limit=0.89:level=disabled[aout]"
    )

    filter_complex = f"{video_filter};{audio_filter}"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-t", f"{total_duration:.2f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg multiclip compose failed:\n{result.stderr[-3000:]}")


def _kill_process_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            text=True,
        )
    else:
        proc.kill()


def _run(cmd: list, *, timeout: int | float = 120) -> None:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        raise RuntimeError(
            f"Command timed out after {timeout}s: {' '.join(str(c) for c in cmd)}\n"
            f"{(stderr or '')[-3000:]}"
        )

    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(str(c) for c in cmd)}\n{stderr}"
        )
