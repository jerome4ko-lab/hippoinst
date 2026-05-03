"""One-time helper: Google OAuth 흐름을 돌려 YOUTUBE_REFRESH_TOKEN을 발급한다.

사전 준비
─────────────────────────────
1. https://console.cloud.google.com 에서 프로젝트 생성/선택
2. "API 및 서비스 → 라이브러리" → 'YouTube Data API v3' 활성화
3. "API 및 서비스 → OAuth 동의 화면"
   - User Type: 외부
   - 테스트 사용자 목록에 본인 Google 계정 추가
   - Scope에 `https://www.googleapis.com/auth/youtube.upload` 추가
4. "API 및 서비스 → 사용자 인증 정보 → 사용자 인증 정보 만들기"
   - OAuth 클라이언트 ID, 애플리케이션 유형 = '데스크톱 앱'
   - 생성 후 JSON 다운로드 → 이 폴더(tools/)에 `client_secret.json` 으로 저장

실행
─────────────────────────────
    python tools/youtube_authorize.py

브라우저가 열리며 Google 계정 로그인 + 권한 승인 → 콘솔에 출력된
YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN 세 줄을
프로젝트 루트 .env 에 추가하면 끝. 한 번만 하면 됨.
"""
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",   # 통계 조회 (Phase 6)
]
SECRET = Path(__file__).parent / "client_secret.json"


def main() -> None:
    if not SECRET.exists():
        print(f"[ERROR] {SECRET} 가 없어요.")
        print("   Google Cloud Console에서 'OAuth 클라이언트 ID(데스크톱 앱)' JSON을")
        print("   다운로드해 위 경로에 client_secret.json 으로 저장한 뒤 다시 실행하세요.")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(SECRET), SCOPES)
    creds = flow.run_local_server(
        port=0,
        prompt="consent",      # refresh_token 보장
        access_type="offline",
        open_browser=False,    # 브라우저 자동 실행 비활성 — 사용자가 URL을 직접 열어 계정/채널을 명시적으로 선택
        authorization_prompt_message=(
            "\n" + ("=" * 70) + "\n"
            "[!] 아래 URL 을 복사해서 시크릿 창(또는 의도한 계정이 기본인 브라우저)에 붙여넣으세요.\n"
            "    Google 계정 선택 화면 -> 의도한 계정 -> (필요 시) 채널 선택 -> 권한 승인\n"
            + ("=" * 70) + "\n\n{url}\n"
        ),
        success_message="[OK] 인증 완료. 이 탭은 닫으셔도 됩니다.",
    )

    info = json.loads(SECRET.read_text(encoding="utf-8"))
    inst = info.get("installed") or info.get("web") or {}

    print()
    print("=" * 60)
    print("[OK] 인증 성공! 아래 3줄을 프로젝트 루트의 .env 파일에 추가하세요.")
    print("=" * 60)
    print(f"YOUTUBE_CLIENT_ID={inst.get('client_id', '?')}")
    print(f"YOUTUBE_CLIENT_SECRET={inst.get('client_secret', '?')}")
    print(f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 60)
    print()
    print("[!] refresh_token 은 보안 정보입니다. .env는 .gitignore에 있어야 해요.")


if __name__ == "__main__":
    main()
