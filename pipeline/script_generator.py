import json
from pathlib import Path
import anthropic
import config

_SYSTEM = """당신은 힙포인사이트(@hippoinst) 유튜브 쇼츠 스크립트 작가입니다.
AI·로봇·미래 신기술 채널을 위한 55초 쇼츠 스크립트를 작성합니다.
규칙: 쉽고 친근한 한국어, 과장 없이 임팩트 있게, 숫자/데이터 포함으로 신뢰도 상승."""

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
- subtitles의 text를 모두 이어붙이면 narration 전체 텍스트와 일치해야 합니다"""


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
