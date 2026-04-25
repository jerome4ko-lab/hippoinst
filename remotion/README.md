# hippoinst-remotion

Remotion 기반 렌더러 실험 브랜치. Python 파이프라인이 산출하는 자산(JSON + 클립/TTS/BGM)을 받아 React 컴포지션으로 합성합니다.

## 디렉터리 구조

```
remotion/
├── package.json
├── remotion.config.ts
├── tsconfig.json
├── src/
│   ├── index.ts          # registerRoot
│   ├── Root.tsx          # <Composition /> 등록
│   └── HippoShort.tsx    # 1080x1920 메인 컴포지션
└── public/
    └── preview.json      # 스튜디오 프리뷰 샘플 props
```

## 셋업

```
cd remotion
npm install
```

## 스튜디오 (실시간 미리보기)

```
npm run studio
```

브라우저에서 컴포지션 선택 → `public/preview.json` 데이터로 렌더 미리보기.

## CLI 렌더

샘플 props:

```
npm run render
```

자체 props로 렌더:

```
npx remotion render HippoShort out/video.mp4 --props=/abs/path/to/props.json
```

## Props 스키마 (Python ↔ Remotion 인터페이스)

```ts
type SubtitleChunk = { text: string; start: number; end: number };

type ShortProps = {
  hook: string;
  hashtags: string;
  bgImageSrc: string | null;   // 사전 렌더된 배경 PNG (banner+hook+hashtag) — null이면 React로 합성
  clipSrc: string | null;      // 트림된 영상 mp4
  ttsSrc: string | null;       // ElevenLabs TTS mp3
  bgmSrc: string | null;       // BGM mp3
  bgmVolume: number;           // 0..1 (기본 0.08)
  ttsVolume: number;           // 0..1 (기본 1.0)
  durationInSeconds: number;   // 영상 총 길이
  subtitles: SubtitleChunk[];  // 의미 단위 자막 청크 + TTS 정렬된 start/end
};
```

`bgImageSrc/clipSrc/ttsSrc/bgmSrc`는 `staticFile()` 친화 경로(`remotion/public/...`) 또는 절대 URL/파일 경로.

## Python 파이프라인 통합 (예정)

다음 단계에서 추가:
1. `pipeline/remotion_render.py` — props.json 빌더 + remotion CLI 호출
2. 자산을 `remotion/public/`로 심볼릭 복사 또는 절대 경로 주입
3. `web/app.py`에 ffmpeg 합성 vs Remotion 합성 토글 (실험용)
