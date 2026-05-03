"""Telegram 알림 (stdlib urllib 만 사용 — 외부 의존성 0).

설계 원칙
─────────
- 환경변수 미설정 시 silently no-op (서비스 동작 영향 X)
- 5회 연속 실패하면 자동 비활성화 (잘못된 chat_id 무한 실패 방지)
- HTML parse_mode 기본 (특수문자 escape 처리 포함)

사용 예
─────────
    from pipeline import notifier
    notifier.notify_upload_success(record)
    notifier.notify_upload_failed(record)
    notifier.send_telegram("아무 텍스트")
"""
from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from typing import Optional

import config


_API_BASE = "https://api.telegram.org"
_TIMEOUT_S = 10

# 연속 실패 카운터 — 5회 연속 fail 시 토큰 만료/잘못된 chat_id 가능성 → 자동 비활성화
_fail_lock = threading.Lock()
_fail_count = 0
_disabled_due_to_fails = False
_FAIL_THRESHOLD = 5


def is_enabled() -> bool:
    """알림이 활성화되어 있는지 (설정 + 자동 비활성 모두 고려)."""
    if _disabled_due_to_fails:
        return False
    return bool(
        config.TELEGRAM_BOT_TOKEN
        and config.TELEGRAM_CHAT_ID
        and config.TELEGRAM_NOTIFY
    )


def _record_failure() -> None:
    global _fail_count, _disabled_due_to_fails
    with _fail_lock:
        _fail_count += 1
        if _fail_count >= _FAIL_THRESHOLD and not _disabled_due_to_fails:
            _disabled_due_to_fails = True
            print(f"[telegram] {_FAIL_THRESHOLD}회 연속 실패 - 자동 비활성화 (서버 재시작 시 다시 시도)")


def _record_success() -> None:
    global _fail_count
    with _fail_lock:
        _fail_count = 0


def _escape_html(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _chat_ids() -> list[str]:
    """TELEGRAM_CHAT_ID 를 쉼표로 분리해 리스트로 반환."""
    raw = config.TELEGRAM_CHAT_ID or ""
    return [cid.strip() for cid in raw.split(",") if cid.strip()]


def _send_one(chat_id: str, text: str, parse_mode: str, disable_web_page_preview: bool) -> bool:
    """단일 chat_id 에 메시지 발송. 성공 True / 실패 False."""
    url = f"{_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id":                  chat_id,
        "text":                     text,
        "parse_mode":               parse_mode,
        "disable_web_page_preview": "true" if disable_web_page_preview else "false",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as e:                                          # noqa: BLE001
        print(f"[telegram] send error (chat_id={chat_id}): {e}")
        return False
    if not body.get("ok"):
        print(f"[telegram] api error (chat_id={chat_id}): {body!r}")
        return False
    return True


def send_telegram(
    text: str,
    *,
    parse_mode: str = "HTML",
    disable_web_page_preview: bool = False,
) -> bool:
    """Telegram 메시지 발송 — TELEGRAM_CHAT_ID 의 모든 채팅방에 각각 전송.
    하나라도 성공하면 True, 전부 실패하면 False."""
    if not is_enabled():
        return False

    ids = _chat_ids()
    if not ids:
        return False

    any_ok = False
    any_fail = False
    for cid in ids:
        ok = _send_one(cid, text, parse_mode, disable_web_page_preview)
        if ok:
            any_ok = True
        else:
            any_fail = True

    if any_fail:
        _record_failure()
    if any_ok:
        _record_success()
    return any_ok


# ── Domain helpers ───────────────────────────────────────────────────────────

def notify_upload_success(rec: dict) -> bool:
    title  = rec.get("title") or "(제목없음)"
    url    = rec.get("video_url") or ""
    privacy = rec.get("privacy_status") or "private"
    privacy_emoji = {"public": "🌐 공개", "unlisted": "🔗 일부 공개", "private": "🔒 비공개"}.get(privacy, privacy)
    text = (
        f"🎬 <b>{_escape_html(title)}</b>\n"
        f"✅ YouTube 업로드 완료 — {privacy_emoji}\n"
        f"🔗 {url}"
    )
    return send_telegram(text)


def notify_upload_failed(rec: dict) -> bool:
    title = rec.get("title") or "(제목없음)"
    err   = rec.get("error") or "unknown"
    text = (
        f"❌ <b>{_escape_html(title)}</b>\n"
        f"YouTube 업로드 실패\n"
        f"사유: <code>{_escape_html(err)}</code>"
    )
    return send_telegram(text)


def status_summary() -> dict:
    """관리 탭에서 상태 표시용."""
    return {
        "configured": bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID),
        "enabled":    is_enabled(),
        "auto_disabled": _disabled_due_to_fails,
        "fail_count": _fail_count,
    }
