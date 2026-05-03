"""YouTube Data API v3 — 영상 업로드.

OAuth 2.0 Desktop 흐름으로 한 번 발급받은 refresh_token을 .env에 저장해두고,
이후 매 업로드마다 access_token을 자동 갱신해 사용한다.

사전 준비: tools/youtube_authorize.py 참고.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Callable

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

import config


# 업로드(youtube.upload) + 통계 조회(youtube.readonly) 둘 다 필요.
# scope 변경 후엔 기존 refresh_token 이 무효화 → tools/youtube_authorize.py 재실행 필요.
_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
_TOKEN_URI = "https://oauth2.googleapis.com/token"

# YouTube category IDs — KR 기준 자주 쓰는 것
CATEGORY_IDS = {
    "science_tech":  "28",   # 과학·기술 (기본)
    "entertainment": "24",
    "education":     "27",
    "people_blogs":  "22",
    "news":          "25",
    "howto_style":   "26",
    "gaming":        "20",
    "music":         "10",
}


def _yt_client():
    if not (config.YOUTUBE_CLIENT_ID and config.YOUTUBE_CLIENT_SECRET and config.YOUTUBE_REFRESH_TOKEN):
        raise RuntimeError(
            "YouTube 인증 정보가 없어요. tools/youtube_authorize.py 를 먼저 실행해 "
            ".env에 YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN 을 저장하세요."
        )
    creds = Credentials(
        token=None,
        refresh_token=config.YOUTUBE_REFRESH_TOKEN,
        token_uri=_TOKEN_URI,
        client_id=config.YOUTUBE_CLIENT_ID,
        client_secret=config.YOUTUBE_CLIENT_SECRET,
        scopes=_SCOPES,
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_video(
    file_path: Path,
    *,
    title: str,
    description: str = "",
    tags: Optional[list[str]] = None,
    category_id: str = "28",
    privacy_status: str = "private",   # private | unlisted | public
    made_for_kids: bool = False,
    progress_cb: Optional[Callable[[float], None]] = None,
) -> dict:
    """영상 파일을 YouTube에 업로드. 성공 시 {video_id, url} 반환."""
    if not Path(file_path).exists():
        raise FileNotFoundError(f"파일을 찾을 수 없어요: {file_path}")

    if privacy_status not in ("private", "unlisted", "public"):
        raise ValueError(f"privacy_status 는 private/unlisted/public 중 하나여야 해요: {privacy_status}")

    yt = _yt_client()
    body = {
        "snippet": {
            "title":       (title or "")[:100] or "제목 없음",
            "description": (description or "")[:5000],
            "tags":        list(tags or [])[:30],
            "categoryId":  category_id,
        },
        "status": {
            "privacyStatus":           privacy_status,
            "selfDeclaredMadeForKids": bool(made_for_kids),
        },
    }

    media = MediaFileUpload(
        str(file_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 5,    # 5 MB chunks
    )
    request = yt.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
            if status and progress_cb:
                progress_cb(float(status.progress()))
    except HttpError as e:
        # 사용자에게 보일 만한 메시지로 다듬기
        msg = getattr(e, "_get_reason", lambda: str(e))()
        raise RuntimeError(f"YouTube API 오류: {msg}") from e

    video_id = response.get("id")
    return {
        "video_id": video_id,
        "url":      f"https://www.youtube.com/watch?v={video_id}",
    }


# ── Read API: video statistics (Phase 6) ─────────────────────────────────────

def fetch_video_stats(video_ids: list[str]) -> dict[str, dict]:
    """video_id → {viewCount, likeCount, commentCount, favoriteCount} 매핑.

    YouTube API 는 한 호출에 최대 50개 ID 까지. 이 함수는 자동으로 배치 분할.
    실패한 ID 는 결과에 포함되지 않음 (조용히 누락).
    """
    if not video_ids:
        return {}

    yt = _yt_client()
    out: dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):
        batch = [vid for vid in video_ids[i:i + 50] if vid]
        if not batch:
            continue
        try:
            resp = yt.videos().list(
                part="statistics",
                id=",".join(batch),
                maxResults=50,
            ).execute()
        except HttpError as e:
            msg = getattr(e, "_get_reason", lambda: str(e))()
            raise RuntimeError(f"YouTube API 오류 (videos.list): {msg}") from e

        for item in resp.get("items", []):
            vid = item.get("id")
            stats = item.get("statistics") or {}
            if not vid:
                continue
            out[vid] = {
                "viewCount":     int(stats.get("viewCount") or 0),
                "likeCount":     int(stats.get("likeCount") or 0),
                "commentCount":  int(stats.get("commentCount") or 0),
                "favoriteCount": int(stats.get("favoriteCount") or 0),
            }
    return out
