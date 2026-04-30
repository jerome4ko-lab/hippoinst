import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import anthropic
import config
from pipeline.article_fetch import is_url, fetch_article_text  # noqa: F401

_VARIANT_DIRECTIVES = {
    "A": "[VARIANT A] 첫 줄 오프닝은 4-3의 '장면 던지기형'으로 작성하세요.",
    "B": "[VARIANT B] 첫 줄 오프닝은 4-3의 '숫자 충격형' 또는 '역설형'으로 작성하세요.",
}

# ── 스킬 문서 로드 ─────────────────────────────────────────────────────────────
_SKILL_PATH = Path(__file__).parent / "hippo_script_skill.md"

def _load_skill() -> str:
    if _SKILL_PATH.exists():
        return _SKILL_PATH.read_text(encoding="utf-8")
    return ""

# ── 시스템 프롬프트 ────────────────────────────────────────────────────────────
_SYSTEM_TEMPLATE = """당신은 힙포인사이트(@hippoinst) 유튜브 쇼츠 스크립트 작가입니다.
아래 스킬 문서의 모든 규칙을 엄격히 따라 스크립트를 작성하세요.

{skill_doc}"""

def _build_system() -> str:
    return _SYSTEM_TEMPLATE.format(skill_doc=_load_skill())

# ── JSON 포맷 지시 ─────────────────────────────────────────────────────────────
_JSON_INSTRUCTION = """
JSON 형식으로만 응답하세요 (마크다운 코드 블록 제외).
스킬 문서의 '3-1. JSON 출력 형식'을 그대로 따르세요."""

DEFAULT_HASHTAGS = "#AI #로봇 #미래기술 #힙포인사이트"
DEFAULT_HOOK = "힙포인사이트|쇼츠"

# ── 유틸 ──────────────────────────────────────────────────────────────────────
def load_articles(path: Path) -> list[str]:
    """Parse articles.txt separated by --- markers."""
    raw = path.read_text(encoding="utf-8")
    articles = [a.strip() for a in raw.split("---") if a.strip()]
    return [a for a in articles if not a.startswith("여기에 기사")]

def _expand_articles(articles: list[str]) -> tuple[list[tuple[int, str]], list[str]]:
    """URL 항목은 fetch+추출, 텍스트는 그대로. 부분 성공 허용.

    Returns (used, warnings):
        used     = [(원래 1-based 번호, 본문 텍스트), ...]
        warnings = ["기사 N 가져오기 실패: ...", ...]
    """
    used: list[tuple[int, str]] = []
    warnings: list[str] = []
    for i, a in enumerate(articles):
        s = (a or "").strip()
        if not s:
            continue
        if is_url(s):
            try:
                text = fetch_article_text(s, idx=i)
                used.append((i + 1, text))
            except Exception as e:
                warnings.append(str(e))
        else:
            used.append((i + 1, s))
    return used, warnings

# ── 스크립트 생성 ──────────────────────────────────────────────────────────────
def _build_articles_prompt(used: list[tuple[int, str]]) -> str:
    numbered = "\n\n".join(f"[기사 {n}]\n{text}" for n, text in used)
    return f"""다음 기사들을 읽고 가장 임팩트 있는 핵심 사실 하나를 골라 55초 쇼츠 스크립트를 작성해주세요.

{numbered}
{_JSON_INSTRUCTION}"""


def generate_two_variants_from_articles(articles: list[str]) -> dict:
    """A·B 두 버전을 병렬로 생성. A=장면 던지기형, B=숫자 충격형/역설형 오프닝."""
    used, warnings = _expand_articles(articles)
    if not used:
        if warnings:
            raise RuntimeError(warnings[0])
        raise RuntimeError("기사를 입력해주세요")

    user_prompt = _build_articles_prompt(used)

    # 두 호출은 같은 system + user prompt 를 공유, variant directive 만 다름.
    # 각 호출 ~5-10초 소요되므로 병렬화로 사용자 대기시간 절반.
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = {
            tag: ex.submit(_call_claude, user_prompt, _VARIANT_DIRECTIVES[tag])
            for tag in ("A", "B")
        }
        scripts = [futures["A"].result(), futures["B"].result()]

    return {
        "scripts":  scripts,
        "used":     [n for n, _ in used],
        "warnings": warnings,
    }


def generate_script_from_articles(articles: list[str]) -> dict:
    """단일 스크립트 wrapper — CLI/배경 파이프라인 호환용. A버전(장면 던지기형) 사용."""
    result = generate_two_variants_from_articles(articles)
    return {
        "script":   result["scripts"][0],
        "used":     result["used"],
        "warnings": result["warnings"],
    }


def generate_script(topic: str) -> dict:
    raise RuntimeError("정보가 너무 부족해요. 기사를 추가해주세요.")


def normalize_hook_candidate(candidate) -> str:
    """Convert a legacy hook object into the editor/render `white|yellow` form."""
    if isinstance(candidate, dict):
        white = str(candidate.get("white") or candidate.get("main") or "").strip()
        yellow = str(
            candidate.get("yellow")
            or candidate.get("gold")
            or candidate.get("accent")
            or ""
        ).strip()
        if white or yellow:
            return f"{white}|{yellow}"
        return ""
    return str(candidate or "").strip()


def select_hook(script: dict | None, fallback: str = DEFAULT_HOOK) -> str:
    script = script or {}
    explicit = str(script.get("hook") or "").strip()
    if explicit:
        return explicit
    legacy_key = "hook" + "_candidates"
    for candidate in script.get(legacy_key) or []:
        hook = normalize_hook_candidate(candidate)
        if hook:
            return hook
    return fallback


def normalize_narration_lines(narration) -> list[str]:
    if isinstance(narration, str):
        return [line.strip() for line in narration.splitlines() if line.strip()]
    return [str(line).strip() for line in (narration or []) if str(line).strip()]


def _normalize_hook_options(raw, limit: int) -> list[str]:
    """Trim/dedupe hook idea chips for the editor inspiration row."""
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        s = str(item or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s[:limit] if len(s) > limit else s)
        if len(out) >= 3:
            break
    return out


def normalize_script_shape(script: dict | None) -> dict:
    """Fill required consumer fields for the single-hook script schema."""
    data = dict(script or {})
    data["hook"] = select_hook(data)
    data["hook_white_options"] = _normalize_hook_options(data.get("hook_white_options"), 18)
    data["hook_gold_options"] = _normalize_hook_options(data.get("hook_gold_options"), 14)
    data.pop("hook" + "_candidates", None)
    data.pop("ending" + "_candidates", None)
    data.pop("selected" + "_ending", None)
    data["hashtags"] = str(data.get("hashtags") or DEFAULT_HASHTAGS).strip()
    data["bgm_tag"] = str(data.get("bgm_tag") or "bgm_future").strip()
    data["narration"] = normalize_narration_lines(data.get("narration") or [])
    return data


# ── Claude 호출 ───────────────────────────────────────────────────────────────
def _call_claude(user_prompt: str, variant_directive: str = "") -> dict:
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    if variant_directive:
        user_prompt = f"{variant_directive}\n\n{user_prompt}"
    msg = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=2048,
        system=_build_system(),
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()
    return normalize_script_shape(json.loads(raw))
