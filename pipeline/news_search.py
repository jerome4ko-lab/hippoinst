"""Anthropic Claude + web_search 도구로 뉴스 검색.

사용자 정형 프롬프트(휴머노이드 우선순위, 가상 URL 금지, 5개 링크,
YouTube 검색 키워드, 쇼츠 핵심 포인트)를 그대로 적용하고
시간 역순으로 5~8개 토픽을 반환한다.
"""
from __future__ import annotations

import json
import re

import anthropic

import config


_SYSTEM_BASE = """당신은 힙포인사이트(@hippoinst) 채널의 뉴스 큐레이터입니다.
주어진 주제에 대해 최근 한 달 이내의 핵심 뉴스를 web_search 도구로 직접 찾아,
시청자의 호기심을 자극하는 쇼츠 소재로 정리합니다.

⚠️ 중요 규칙:
- 실제로 확인된 뉴스만 포함할 것
- 기사 URL은 web_search로 실존 확인된 것만 기재
- 불확실하거나 검색되지 않은 링크는 정확히 "검색 필요" 라는 문자열로 표기
- 가상/추정 URL은 절대 만들지 말 것 (조작은 결격 사유)
- 모든 항목은 최초 보도일 기준 시간 역순(date desc)으로 정렬

조회수 유발 요소:
- 충격적 기록 경신, 인간과의 비교, "처음으로~" 서사
- 구체적 수치(속도·적재량·관절수·생산량 등) 포함
"""

_ROBOT_PRIORITY = """
주제 우선순위(휴머노이드/로봇):
- 하드웨어 성능 변화 (속도·적재량·관절수·자유도 등 수치)
- 실제 스포츠·가사·군사·산업 현장에서의 시연
- 대규모 생산·상용화 돌파구
"""

_AI_PRIORITY = """
주제 우선순위(AI):
- 모델·제품·기능의 명확한 돌파구 (벤치마크 신기록, 새 기능)
- 실제 산업/제품 적용 사례, 대규모 도입
- 정책·규제·소송 등 산업 영향이 큰 사건
"""

_OUTPUT_FORMAT = """
JSON으로만 응답하세요(마크다운/설명 일체 없이, 코드블록 사용 금지):
{
  "items": [
    {
      "date":        "YYYY-MM-DD",
      "title":       "쇼츠용 호기심 자극 한국어 제목 (25자 이내 권장)",
      "keyword":     "YouTube 검색 키워드 (영어 권장)",
      "summary":     "구체적 수치 포함 2~3문장 (한국어)",
      "links":       ["https://...", "검색 필요", "..."],
      "youtube_url": "https://www.youtube.com/watch?v=... (롱폼 원본, 모르면 빈 문자열)"
    }
  ]
}

- items 길이는 5~8개
- links는 항목당 최대 5개. 실존 URL과 "검색 필요"만 허용
- date는 YYYY-MM-DD 형식 필수
"""

_TOPIC_QUERY = {
    "robot": "휴머노이드 로봇 / humanoid robot 최근 한 달 핵심 뉴스",
    "ai":    "AI 기술·제품·사건 최근 한 달 핵심 뉴스",
}


def search_news(topic: str, *, limit: int = 7, **_ignored) -> list[dict]:
    """Claude API + web_search 도구로 뉴스 검색.

    `limit` 은 모델에 토픽 개수 힌트로 전달. **_ignored 는 구버전 호환
    (freshness 등 호출자 인자 무해 흡수).
    """
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 비어있어요")

    priority = _ROBOT_PRIORITY if topic == "robot" else _AI_PRIORITY
    system   = _SYSTEM_BASE + priority + _OUTPUT_FORMAT

    user_msg = (
        f"주제: {_TOPIC_QUERY.get(topic, topic)}\n"
        f"web_search 도구로 최근 한 달 이내 가장 핵심적인 뉴스를 찾아 "
        f"{max(5, min(int(limit), 8))}개 토픽으로 정리해주세요.\n"
        f"각 토픽의 links 배열에는 실존 확인된 URL을 최대 5개까지 채우고, "
        f"확신 없는 슬롯은 정확히 \"검색 필요\" 문자열로 두세요."
    )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=4096,
        system=system,
        tools=[{
            "type":     "web_search_20250305",
            "name":     "web_search",
            "max_uses": 5,
        }],
        messages=[{"role": "user", "content": user_msg}],
    )

    raw  = _extract_text(msg)
    data = _parse_json(raw)
    items = data.get("items") or []

    # 시간 역순 한 번 더 보정 (모델 정렬 누락 대비)
    items.sort(key=lambda x: (x.get("date") or ""), reverse=True)
    return items


# ── helpers ──────────────────────────────────────────────────────────────────

def _extract_text(msg) -> str:
    """messages.create 응답에서 마지막 text 블록만 뽑아온다.
    web_search 사용 시 tool_use / tool_result 블록이 섞일 수 있음.
    """
    texts: list[str] = []
    for block in (msg.content or []):
        block_type = getattr(block, "type", None)
        if block_type == "text":
            texts.append(getattr(block, "text", "") or "")
    if not texts:
        raise RuntimeError("Claude 응답에 text 블록이 없어요")
    return texts[-1].strip()


def _parse_json(raw: str) -> dict:
    s = raw.strip()
    # 안전망: 모델이 코드블록을 흘렸을 때 떼어내기
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    # 첫 { ~ 마지막 } 만 잘라 JSON으로 보정
    start = s.find("{")
    end   = s.rfind("}")
    if start >= 0 and end > start:
        s = s[start : end + 1]
    return json.loads(s)
