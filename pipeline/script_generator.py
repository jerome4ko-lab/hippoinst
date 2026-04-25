import json
from pathlib import Path
import anthropic
import config

_SYSTEM = """당신은 힙포인사이트(@hippoinst) 유튜브 쇼츠 스크립트 작가입니다.
AI·로봇·미래 신기술 채널을 위한 55초 쇼츠 스크립트를 작성합니다.

스타일: 쉽고 친근한 한국어, 과장 없이 임팩트 있게.

★ 가장 중요한 원칙 — 팩트 엄수:
- 제공된 기사 본문에 명시되어 있는 사실·숫자·이름·날짜·기관만 사용
- 기사에 없는 수치(반응속도, 가격, 출시일, 회사명 등)는 절대 만들지 말 것
- 일반 상식이라도 기사에 없으면 빼거나 "~한대요" 같은 전언 표현으로 처리
- 추측·과장·일반화("앞으로 모든 직업이...") 금지
- 의심스러우면 그 줄을 빼고 다른 사실 한 줄로 대체"""

_JSON_FORMAT = """
JSON 형식으로만 응답하세요 (마크다운 코드 블록 제외):
{
  "hook": "화면 상단에 표시할 짧은 훅 (25자 이내)",
  "hashtags": "#AI #로봇 #미래기술 #힙포인사이트",
  "bgm_tag": "bgm_future",
  "narration": [
    "호기심을 자극하는 첫 줄 (의문문 or 충격 사실)",
    "구체적인 행동/상황 묘사 1",
    "구체적인 행동/상황 묘사 2",
    "구체적인 행동/상황 묘사 3",
    "반전 또는 핵심 포인트",
    "기술 설명 1",
    "기술 설명 2",
    "시청자에게 던지는 질문으로 마무리 (CTA)"
  ],
  "subtitles": [
    {"text": "이 로봇은"},
    {"text": "단 하루 만에"},
    {"text": "작업을 학습합니다"}
  ],
  "gifs": [
    {"keyword_en": "robot dancing", "start": 12.5, "duration": 2.0},
    {"keyword_en": "facepalm", "start": 28.0, "duration": 1.8}
  ]
}

규칙:
- narration은 14~22줄, 한 줄당 20~35자 (55초·읽기 속도 1.2배 기준)
- 각 줄은 짧고 구체적으로 (행동, 숫자, 장면 묘사 위주)
- 말투: ~요 / ~해요 / ~했는데요 / ~죠 / ~다 를 자연스럽게 섞어서 사용. ~다.로만 끝나지 않도록
- 친근하고 자연스러운 구어체, 유튜브 나레이션처럼 생동감 있게
- 마지막 줄은 반드시 시청자 질문 ("여러분은 ~?" 형식)
- bgm_tag: bgm_impact(충격·군사·급변) / bgm_light(귀엽·생활·발명) / bgm_future(AI·우주·미래도시)

자막 청크(subtitles) 규칙:
- narration 전체 내용을 그대로 화면 단위로 쪼갠 리스트
- 한 청크 3~8자 (공백 제외), 의미 단위로 자연스럽게 끊기
- 너무 길거나 너무 짧은 줄 없이 균등하게
- start/end 같은 시간값은 넣지 마세요 (음성 alignment에서 자동 산출)
- subtitles의 text를 모두 이어붙이면 narration 전체 텍스트와 일치해야 합니다

GIF 오버레이(gifs) 규칙 — 영상 재미용 리액션 GIF (필수, 최소 1개):
- keyword_en: 반드시 영어 키워드 (Klipy 검색 hit율↑). 짧고 시각적으로 강한 단어 — "robot dancing", "wow", "facepalm", "mind blown", "mic drop", "explosion", "thumbs up", "shocked", "amazing", "no way" 등.
- start: narration 흐름상 그 표현이 어울리는 순간의 초 단위 시각 (대략 추정 OK — TTS 길이는 narration 글자수 기반 ~3.5자/초로 가늠).
- duration: 1.5~3.0초. 너무 길면 지루.
- **반드시 1~3개 포함**. 영상 임팩트의 핵심이라 0개로 두지 말 것. 모든 주제(진지·뉴스·기술 포함)에 어울리는 GIF가 존재함.
- start 값들이 너무 가깝지 않게 (최소 5초 간격).
- 첫 GIF는 영상의 hook/반전 지점, 두 번째는 마무리 직전 강조 지점에 배치."""


def load_articles(path: Path) -> list[str]:
    """Parse articles.txt separated by --- markers."""
    raw = path.read_text(encoding="utf-8")
    articles = [a.strip() for a in raw.split("---") if a.strip()]
    # Filter out placeholder lines
    return [a for a in articles if not a.startswith("여기에 기사")]


def generate_script_from_articles(articles: list[str]) -> dict:
    numbered = "\n\n".join(f"[기사 {i+1}]\n{a}" for i, a in enumerate(articles))
    prompt = f"""다음 기사들을 읽고 가장 임팩트 있는 핵심 사실 하나를 골라 55초 쇼츠 스크립트를 작성해주세요.

{numbered}

{_JSON_FORMAT}"""
    return _call_claude(prompt)


def generate_script(topic: str) -> dict:
    prompt = f"""다음 주제로 55초 쇼츠 스크립트를 작성해주세요.
주제: {topic}

{_JSON_FORMAT}"""
    return _call_claude(prompt)


_FACTCHECK_SYSTEM = """당신은 힙포인사이트의 팩트체커입니다.
주어진 원본 기사들과 영상 스크립트를 비교하여 각 줄이 기사에 근거하는지 검증합니다.
간결·정확·과장 없는 평가가 핵심입니다."""

_FACTCHECK_FORMAT = """JSON으로만 응답하세요 (마크다운 제외):
{
  "status": "ok | warn | bad",
  "summary": "한 문장 요약 (40자 이내)",
  "issues": [
    {"line": "문제 있는 스크립트 줄 그대로", "reason": "기사에 없는 수치/주장 등 사유 (30자 이내)"}
  ]
}

판정 기준:
- ok: 모든 줄이 기사에 근거. issues 빈 배열.
- warn: 일부 표현이 기사에 직접 명시되진 않았지만 합리적 일반화. issues 1~3개.
- bad: 기사와 충돌하거나 사실이 아닌 주장. issues에 최대 5개까지만.

issues는 최대 5개. 사소한 표현 차이(말투, 어순)는 issue로 잡지 않습니다."""


def fact_check(articles: list[str], script_text: str) -> dict:
    """기사들과 (편집된) 스크립트 텍스트를 받아 팩트 검증 결과 반환."""
    numbered = "\n\n".join(f"[기사 {i+1}]\n{a}" for i, a in enumerate(articles) if a.strip())
    prompt = f"""다음은 원본 기사들과 그 기반으로 만든 영상 스크립트입니다.
스크립트의 각 줄이 기사에 근거하는지 검증해주세요.

──── 원본 기사 ────
{numbered}

──── 검증할 스크립트 ────
{script_text}

{_FACTCHECK_FORMAT}"""
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1024,
        system=_FACTCHECK_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()
    return json.loads(raw)


def _call_claude(user_prompt: str) -> dict:
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=2048,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()
    return json.loads(raw)
