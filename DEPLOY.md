# HippoInst 집 서버 Docker 배포 가이드

대상 서버:

- Ubuntu 26.04 LTS
- Docker 29.1.3 / Docker Compose v2.40.3
- 내부 IP: `192.168.0.42`
- DDNS: `jerome-server.iptime.org`
- 앱 포트: `8420` (`4173`은 kanban 앱이 사용 중이라 피함)

## 1. 서버에 코드 받기

```bash
ssh jerome@192.168.0.42
mkdir -p ~/apps
cd ~/apps
git clone <REPO_URL> hippoinst
cd hippoinst
```

이미 clone 되어 있으면:

```bash
cd ~/apps/hippoinst
git pull
```

## 2. 런타임 디렉터리 준비

`docker-compose.yml`은 영상/임시파일/업로드 기록을 호스트의 `runtime/` 아래에 보관한다.

```bash
mkdir -p runtime/output runtime/temp runtime/data
```

역할:

- `runtime/output` → 완성된 mp4 영상
- `runtime/temp` → 다운로드 클립, TTS 캐시, 미리보기 등 임시 파일
- `runtime/data` → YouTube 예약 업로드 기록 (`uploads.json`)

## 3. .env 작성

```bash
cp .env.example .env
nano .env
```

예시:

```env
ANTHROPIC_API_KEY=...

APP_PASSWORD=긴_비밀번호로_변경
AUTH_COOKIE_SECRET=openssl_rand_hex_같은_랜덤값

TTS_PROVIDER=typecast
TYPECAST_API_KEY=...
TYPECAST_VOICE_ID=tc_62d66c3ef075c6ebd4114bd5
TYPECAST_MODEL=ssfm-v21

ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=zgDzx5jLLCqEp6Fl7Kl7

KLIPY_API_KEY=
KLIPY_CUSTOMER_ID=hippoinst

YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REFRESH_TOKEN=

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_NOTIFY=1

TEMP_CLEANUP_ENABLED=1
TEMP_CLEANUP_MAX_AGE_HOURS=72
TEMP_CLEANUP_INTERVAL_HOURS=24
REMOTION_JOB_CLEANUP_MAX_AGE_HOURS=168
```

랜덤 secret 생성:

```bash
openssl rand -hex 32
```

`APP_PASSWORD`가 비어 있으면 인증이 꺼진다. HTTP로 외부 공개할 때는 반드시 설정한다.

## 4. 빌드 및 실행

```bash
docker compose build
docker compose up -d
```

상태 확인:

```bash
docker compose ps
docker compose logs -f hippoinst
```

내부망 테스트:

```bash
curl -I http://192.168.0.42:8420
```

브라우저 또는 폰에서:

```text
http://192.168.0.42:8420
```

로그인 화면이 뜨면 `.env`의 `APP_PASSWORD`를 입력한다.

## 5. 공유기 포트포워딩

ipTIME AX3000M 관리 페이지:

1. 브라우저에서 `http://192.168.0.1` 접속
2. 로그인
3. `NAT/라우터 관리` 또는 `고급 설정` → `포트포워드 설정`
4. 새 규칙 추가
   - 규칙 이름: `hippoinst`
   - 프로토콜: `TCP`
   - 외부 포트: `8420`
   - 내부 IP: `192.168.0.42`
   - 내부 포트: `8420`
5. 저장/적용

외부망, 예를 들어 LTE/5G 폰에서 접속:

```text
http://jerome-server.iptime.org:8420
```

공유기에서 외부 포트를 다르게 쓰고 싶으면 예를 들어 `18420 -> 192.168.0.42:8420`으로 열고, 접속 URL만 `http://jerome-server.iptime.org:18420`으로 바꾸면 된다.

## 6. 운영 명령

재시작:

```bash
docker compose restart hippoinst
```

중지:

```bash
docker compose down
```

업데이트 배포:

```bash
git pull
docker compose build
docker compose up -d
```

최근 로그:

```bash
docker compose logs --tail=200 hippoinst
```

컨테이너 내부 확인:

```bash
docker compose exec hippoinst bash
```

## 7. 영상/임시 파일 관리

앱은 시작 후 백그라운드에서 `runtime/temp`의 오래된 임시 파일을 정리한다.

기본값:

- `TEMP_CLEANUP_MAX_AGE_HOURS=72`: 72시간 지난 임시 파일 정리
- `TEMP_CLEANUP_INTERVAL_HOURS=24`: 24시간마다 실행
- `REMOTION_JOB_CLEANUP_MAX_AGE_HOURS=168`: Remotion job 자산 7일 후 정리

완성 영상은 `runtime/output`에 계속 쌓인다. 자동 삭제하지 않는다. 보존 정책을 정해서 수동으로 정리한다.

예: 90일 지난 mp4 목록 확인

```bash
find runtime/output -type f -name '*.mp4' -mtime +90 -print
```

삭제:

```bash
find runtime/output -type f -name '*.mp4' -mtime +90 -delete
```

디스크 사용량 확인:

```bash
du -sh runtime/output runtime/temp runtime/data
df -h
```

## 8. 트러블슈팅

포트 충돌:

```bash
sudo ss -ltnp | grep ':8420'
```

이미 사용 중이면 `docker-compose.yml`의 포트를 예를 들어 `"8421:8000"`으로 바꾸고 공유기 포트포워딩도 `8421`로 맞춘다.

로그에서 API 키 오류가 보일 때:

```bash
nano .env
docker compose restart hippoinst
```

YouTube 업로드 인증 오류:

```bash
docker compose exec hippoinst python tools/youtube_authorize.py
```

출력된 `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`을 `.env`에 반영한 뒤 재시작한다.

영상 다운로드 실패:

- URL 공개 여부 확인
- 클립 시작/종료 시간이 실제 영상 길이 안에 있는지 확인
- 로그 확인:

```bash
docker compose logs --tail=200 hippoinst
```

외부 접속 실패:

- 내부망에서 `http://192.168.0.42:8420`이 먼저 되는지 확인
- Docker 포트 매핑 확인: `docker compose ps`
- 공유기 포트포워딩의 내부 IP가 `192.168.0.42`인지 확인
- 폰이 Wi-Fi가 아니라 LTE/5G 외부망인지 확인

로그인이 계속 실패:

- `.env`의 `APP_PASSWORD` 값을 확인
- 비밀번호 변경 후 컨테이너 재시작:

```bash
docker compose restart hippoinst
```
