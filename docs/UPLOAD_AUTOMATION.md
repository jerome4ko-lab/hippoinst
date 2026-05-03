# 업로드 자동화 가이드

힙포인사이트 쇼츠 영상의 **YouTube 업로드 자동화**를 위한 운영·셋업 문서.
즉시 업로드 / 예약 업로드 / 통계 트래킹 / Telegram 알림이 한 대시보드에 통합되어 있다.

---

## 1. 사전 셋업 (1회)

### 1.1 Google Cloud Console — YouTube Data API v3 OAuth

1. <https://console.cloud.google.com> → 프로젝트 생성 (예: `hippoinst-yt`).
2. **API 및 서비스 → 라이브러리** → "YouTube Data API v3" 사용 설정.
3. **OAuth 동의 화면**:
   - User Type: **외부**
   - 테스트 사용자에 본인 Google 계정 추가
   - 스코프 **두 개 모두 추가** (필수):
     - `https://www.googleapis.com/auth/youtube.upload`
     - `https://www.googleapis.com/auth/youtube.readonly` ← 통계 조회용
4. **사용자 인증 정보 → OAuth 클라이언트 ID 만들기**
   - 애플리케이션 유형: **데스크톱 앱**
   - JSON 다운로드 → 프로젝트의 `tools/client_secret.json` 으로 저장
5. 인증 흐름 실행:

   ```bash
   python tools/youtube_authorize.py
   ```

   브라우저에서 로그인 + 권한 승인 → 콘솔에 출력된 3줄을 `.env` 에 붙여넣기:

   ```env
   YOUTUBE_CLIENT_ID=...
   YOUTUBE_CLIENT_SECRET=...
   YOUTUBE_REFRESH_TOKEN=...
   ```

> ⚠ 기존에 `youtube.upload` 만으로 인증했다면 스코프 추가 후 **재실행 필수**.
> 기존 refresh_token 은 `youtube.readonly` 권한을 갖지 않아 통계 조회가 실패한다.

> ⚠ **할당량**: `videos.insert` 1회 = 1,600 units. 일일 기본 한도 10,000 units →
> 하루 약 6개 업로드면 한도 끝. 본격 운영 시 [할당량 증액 신청](https://support.google.com/youtube/contact/yt_api_form) 권장.
> `videos.list?part=statistics` 는 1 unit 이라 통계 폴링 비용은 무시 가능.

### 1.2 Telegram 봇 (선택)

1. Telegram 에서 [@BotFather](https://t.me/BotFather) → `/newbot` → 토큰 받기.
2. 본인 봇과 대화 1회 시작 (`/start`).
3. `https://api.telegram.org/bot<TOKEN>/getUpdates` 열어 `chat.id` 확인.
4. `.env` 에 추가:

   ```env
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
   TELEGRAM_NOTIFY=1
   ```

> 미설정 시 알림 기능만 자동 비활성화되며 업로드 기능엔 영향 없음.
> 5회 연속 실패 시 자동 비활성 (잘못된 chat_id 무한 실패 방지) — 서버 재시작 시 재시도.

---

## 2. 운영

### 2.1 서버 기동

```bash
uvicorn web.app:app --host 0.0.0.0 --port 8000 --workers 1
```

> ⚠ **`--workers 1` 필수**. 스케줄러·통계 폴러는 단일 프로세스 가정.
> 멀티 워커로 띄우면 같은 예약을 여러 번 업로드할 위험.

기동 시 콘솔 로그:

```
[startup] N 건의 끊긴 업로드를 'failed' 로 마킹했어요   (해당 시)
[startup] upload-scheduler 데몬 기동 (60초 주기)
[startup] stats-poller 데몬 기동 (30분 주기)
```

### 2.2 두 탭 워크플로우

#### 🎬 영상 제작 탭

뉴스 검색 → 스크립트 생성 → TTS 컨펌 → 멀티클립 합성 → 완성 영상.
완성 영상 카드에 **YouTube 발행** 카드가 노출된다.

발행 시점 토글:

- **⚡ 즉시 업로드** — 그 자리에서 SSE 진행률 표시하며 업로드.
- **📅 예약 업로드** — 일시 입력 후 `[📅 예약 등록]` → 토스트 알림. 기록은 관리 탭에서.

#### 📅 업로드 관리 탭

- **월간 캘린더**: 셀 dot 색상으로 상태 구분 (예약·업로드중·완료·실패·취소).
  날짜 클릭 → 그날의 영상 카드 리스트.
- **새 예약 등록**: `output/` 안의 mp4 중 골라 사후 등록.
- **업로드 이력 테이블**: 모든 record 한눈에 + 조회수·좋아요.
- 카드/행 클릭 → **상세 모달**: 수정·삭제·즉시 업로드·통계 갱신·Telegram 재전송.

### 2.3 상태 머신

```
draft → scheduled → uploading → done
                 ↘ cancelled
                  ↘ failed (재시도 시 scheduled 또는 즉시)
즉시 업로드: scheduled 단계 스킵 (uploading 으로 바로 시작)
```

수정 가능한 상태: `scheduled` 만 (제목·예약시각·공개설정 등 모두).
삭제 차단 상태: `uploading`.
"지금 올리기" 가능 상태: `scheduled` / `failed` / `cancelled`.

---

## 3. 아키텍처

### 3.1 백엔드 모듈

| 파일 | 역할 |
| --- | --- |
| `pipeline/youtube_publisher.py` | `videos.insert` (업로드, resumable) + `videos.list` (통계) |
| `pipeline/upload_store.py` | `data/uploads.json` 영속 저장소, threading.RLock 직렬화, atomic write |
| `pipeline/upload_scheduler.py` | 60초 주기 폴러 데몬 — due 검사 후 업로드 트리거 |
| `pipeline/stats_poller.py` | 30분 주기 통계 폴러 — videos.list 배치 호출 |
| `pipeline/notifier.py` | Telegram sendMessage (stdlib urllib, 의존성 0) |
| `tools/youtube_authorize.py` | OAuth 1회성 인증 헬퍼 |

### 3.2 데이터 모델

`data/uploads.json` (gitignored):

```json
{
  "schema_version": 1,
  "items": [
    {
      "id": "u_5f3a7c12",
      "filename": "20260501_073500_훅키워드.mp4",
      "title": "...",
      "description": "...",
      "tags": [],
      "category_id": "28",
      "privacy_status": "private",
      "made_for_kids": false,
      "scheduled_at": "2026-05-02T08:00:00+09:00",
      "status": "scheduled",
      "video_id": null,
      "video_url": null,
      "uploaded_at": null,
      "error": null,
      "progress": 0,
      "stats": null,
      "stats_fetched_at": null,
      "telegram_notified": false,
      "job_id": null,
      "created_at": "...",
      "updated_at": "..."
    }
  ]
}
```

### 3.3 API 라우트

| Method | Path | 용도 |
| --- | --- | --- |
| POST | `/api/youtube-upload` | 즉시 업로드 (구버전 호환) |
| POST | `/api/uploads/schedule` | 예약 등록 |
| GET | `/api/uploads` | 전체 리스트 (`?status=` 필터) |
| GET | `/api/uploads/{id}` | 단건 상세 |
| PATCH | `/api/uploads/{id}` | 수정 (`scheduled` 만) |
| DELETE | `/api/uploads/{id}` | 영구 삭제 (`uploading` 차단) |
| POST | `/api/uploads/{id}/upload-now` | 예약·실패·취소 항목을 즉시 실행 |
| POST | `/api/uploads/{id}/refresh-stats` | 단일 항목 통계 강제 갱신 |
| POST | `/api/uploads/{id}/notify` | Telegram 수동 재전송 |
| POST | `/api/stats/refresh-all` | 전체 통계 일괄 갱신 |
| GET | `/api/telegram/status` | 알림 활성 상태 조회 |
| GET | `/api/output-files` | `output/` 안 mp4 리스트 |

진행률 SSE 는 기존 `/api/progress/{job_id}` 그대로 사용.

---

## 4. 알려진 한계 / 미구현

- **단일 채널**: refresh_token 1개 고정. 다중 채널은 미지원.
- **썸네일 별도 업로드**: 현재 미지원 (`thumbnails.set` 추가 호출 필요).
- **자막(.srt) 별도 업로드**: 미지원.
- **재생목록 자동 추가**: 미지원.
- **드래그-드롭 캘린더**: 미지원 (모달 폼에서 시각 변경).
- **시계열 통계 누적**: stats 는 마지막 스냅샷만 보관. 추이 추적 시 SQLite 마이그레이션 권장.

---

## 5. 트러블슈팅

| 증상 | 원인/해결 |
| --- | --- |
| `youtube.readonly scope is invalid` | `tools/youtube_authorize.py` 재실행으로 refresh_token 재발급 |
| `HttpError 403 quotaExceeded` | 일일 quota 한도. 다음 날 PT 기준 0시 리셋 또는 증액 신청 |
| `파일 없음: ...` | mp4 가 `output/` 에서 삭제됨. 모달에서 재예약 필요 |
| `server_restart_interrupted` | 업로드 중 서버 재시작 → 자동 failed 마킹. 모달에서 "지금 올리기" 로 재시도 |
| Telegram "📨 일시 비활성" 배지 | 5회 연속 실패. chat_id 또는 토큰 확인 후 서버 재시작 |
| 예약 시간이 지났는데 안 올라감 | 폴러 60초 주기. KST 시간대 일치 확인 (`scheduled_at` 의 offset) |
| 통계가 `–` 로 표시 | `done` 항목만 통계 대상. `🔄 통계 새로고침` 또는 30분 자동 폴링 대기 |
