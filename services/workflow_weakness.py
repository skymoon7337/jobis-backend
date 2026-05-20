import re
from typing import Any

from db.repository import (
    delete_learning_data,
    get_memory_items,
    get_weakness_items,
    search_memory_items,
    upsert_memory_item,
    upsert_weakness_item,
)
from services.workflow_common import WorkflowResult, default_user_key, get_status


WEAKNESS_CATEGORIES = ("CS", "언어", "기술스택", "프로젝트/GitHub", "프로젝트/자소서", "답변 태도")


def _clean_weakness_line(line: str) -> str:
    cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line.strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -:")


def _split_weakness_lines(weakness_summary: str) -> list[str]:
    lines = [_clean_weakness_line(line) for line in weakness_summary.splitlines()]
    result: list[str] = []
    for line in lines:
        if not line:
            continue
        if line in {"약점 요약", "다음 준비", "전체 면접 피드백"}:
            continue
        if len(line) < 6:
            continue
        result.append(line)
    if result:
        return result[:5]

    cleaned = _clean_weakness_line(weakness_summary)
    return [cleaned] if cleaned else []


def _infer_topic(line: str) -> str:
    label_match = re.match(r"([^:：-]{2,40})\s*[:：-]\s*(.+)", line)
    if label_match:
        return label_match.group(1).strip()

    quoted_match = re.search(r"['\"“”‘’]([^'\"“”‘’]{2,40})['\"“”‘’]", line)
    if quoted_match:
        return quoted_match.group(1).strip()

    keyword_patterns = (
        r"(트랜잭션|격리\s*수준|동시성|락|인덱스|캐시|OAuth|JWT|테스트|장애\s*대응|배포|성능|보안)",
        r"(Spring|Django|FastAPI|React|Next\.?js|Python|Java|Kotlin|TypeScript|SQL)",
    )
    for pattern in keyword_patterns:
        match = re.search(pattern, line, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()

    return line[:36].strip()


def _infer_category(line: str) -> str:
    lowered = line.lower()
    if _contains_any(lowered, ("github", "깃허브", "프로젝트", "레포", "구현", "커밋")):
        return "프로젝트/GitHub"
    if _contains_any(lowered, ("자소서", "경험", "갈등", "협업", "성과")):
        return "프로젝트/자소서"
    if _contains_any(lowered, ("python", "java", "kotlin", "typescript", "언어", "문법")):
        return "언어"
    if _contains_any(lowered, ("spring", "fastapi", "django", "react", "next", "redis", "mysql", "postgres", "기술스택")):
        return "기술스택"
    if _contains_any(lowered, ("구조", "장황", "두괄식", "논리", "명확", "말", "답변")):
        return "답변 태도"
    return "CS"


def _infer_weakness_type(line: str) -> str:
    lowered = line.lower()
    if _contains_any(lowered, ("구조", "두괄식", "장황", "정리", "흐름")):
        return "답변 구조 부족"
    if _contains_any(lowered, ("근거", "경험", "사례", "예시")):
        return "근거/예시 부족"
    if _contains_any(lowered, ("깊이", "구체", "상세", "원리", "트레이드오프")):
        return "깊이 부족"
    if _contains_any(lowered, ("개념", "정의", "차이", "이해")):
        return "개념 부족"
    return "보완 필요"


def _infer_severity(line: str) -> int:
    lowered = line.lower()
    if _contains_any(lowered, ("반복", "크게", "핵심", "부족", "약함", "혼동", "명확하지")):
        return 4
    if _contains_any(lowered, ("조금", "보완", "다듬")):
        return 2
    return 3


def _suggest_training(topic: str, weakness_type: str, line: str) -> str:
    if weakness_type == "답변 구조 부족":
        return f"{topic} 답변을 결론-근거-예시-회고 순서로 60초 안에 말하는 연습"
    if weakness_type == "근거/예시 부족":
        return f"{topic} 관련 실제 경험, 수치, 선택 이유를 한 가지씩 붙여 답변하는 연습"
    if weakness_type == "깊이 부족":
        return f"{topic}의 원리, 트레이드오프, 대안 비교를 3문장으로 정리"
    if weakness_type == "개념 부족":
        return f"{topic} 핵심 개념과 비슷한 개념의 차이를 예시와 함께 정리"
    return f"{topic} 질문을 다시 받아 짧고 구체적으로 답변하는 연습"


def _contains_any(value: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword.lower() in value for keyword in keywords)


def record_weakness_learning_from_review(
    user_key: str,
    *,
    session_id: int | None,
    analysis_job_id: int | None,
    weakness_summary: str,
    overall_feedback: str = "",
) -> list[dict[str, Any]]:
    source = weakness_summary.strip() or overall_feedback.strip()
    if not source:
        return []

    items: list[dict[str, Any]] = []
    seen_topics: set[tuple[str, str]] = set()
    for line in _split_weakness_lines(source):
        topic = _infer_topic(line)
        category = _infer_category(line)
        key = (topic.lower(), category)
        if key in seen_topics:
            continue
        seen_topics.add(key)
        weakness_type = _infer_weakness_type(line)
        items.append(
            upsert_weakness_item(
                user_key,
                topic=topic,
                category=category,
                weakness_type=weakness_type,
                severity=_infer_severity(line),
                confidence=1,
                evidence=line,
                suggested_training=_suggest_training(topic, weakness_type, line),
                source_session_id=session_id,
                source_analysis_job_id=analysis_job_id,
            )
        )
    return items


def record_interview_turn_memory(
    user_key: str,
    *,
    turn: dict[str, Any],
    session_id: int | None,
) -> dict[str, Any]:
    display_id = str(turn.get("display_id") or "").strip()
    question_type = str(turn.get("question_type") or "미분류").strip()
    question = str(turn.get("question") or "").strip()
    answer = str(turn.get("answer") or "").strip()
    feedback = str(turn.get("feedback") or "").strip()
    source_id = int(turn.get("id") or 0)
    title = f"{display_id or '질문'} {question_type}".strip()
    content = (
        f"질문: {question}\n"
        f"질문 유형: {question_type}\n"
        f"답변: {answer}\n"
        f"피드백: {feedback or '아직 개별 피드백 없음'}"
    )
    summary = f"{question[:80]} / 답변 {len(answer)}자"
    return upsert_memory_item(
        user_key,
        source_type="interview_turn",
        source_id=source_id,
        title=title,
        content=content,
        summary=summary,
        tags=[question_type, "면접 답변", "개별 문답"],
        metadata={
            "session_id": session_id,
            "display_id": display_id,
            "question_type": question_type,
            "is_bonus": bool(turn.get("is_bonus")),
            "bonus_type": str(turn.get("bonus_type") or ""),
        },
    )


def record_final_review_memory(
    user_key: str,
    *,
    session_id: int | None,
    analysis_job_id: int | None,
    answer_feedback: str,
    overall_feedback: str,
    weakness_summary: str,
) -> dict[str, Any] | None:
    content = "\n\n".join(
        part
        for part in (
            f"답변별 피드백\n{answer_feedback.strip()}" if answer_feedback.strip() else "",
            f"전체 리뷰\n{overall_feedback.strip()}" if overall_feedback.strip() else "",
            f"약점 요약\n{weakness_summary.strip()}" if weakness_summary.strip() else "",
        )
        if part
    )
    if not content:
        return None

    return upsert_memory_item(
        user_key,
        source_type="final_review",
        source_id=session_id or analysis_job_id or 0,
        title="최종 면접 리뷰",
        content=content,
        summary=(weakness_summary or overall_feedback or answer_feedback).strip()[:1000],
        tags=["최종 리뷰", "면접 피드백", "약점"],
        metadata={"session_id": session_id, "analysis_job_id": analysis_job_id},
    )


def get_memory_learning(user_key: str | None = None, *, query: str = "") -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    items = search_memory_items(resolved_user_key, query, limit=8) if query.strip() else get_memory_items(resolved_user_key, limit=8)
    if not items:
        return WorkflowResult(
            messages=["아직 찾을 수 있는 면접 기억이 없어. 면접 답변이나 최종 리뷰가 쌓이면 여기서 찾아볼 수 있어."],
            status=get_status(resolved_user_key).status,
            data={"memories": [], "query": query},
        )

    lines = []
    for index, item in enumerate(items[:5], start=1):
        lines.append(f"{index}. {item['title']} - {item['summary'] or item['content'][:90]}")
    prefix = f"`{query}` 관련 기억을 찾았어." if query.strip() else "최근 면접 기억은 이거야."
    return WorkflowResult(
        messages=[prefix + "\n\n" + "\n".join(lines)],
        status=get_status(resolved_user_key).status,
        data={"memories": items, "query": query},
    )


def reset_learning_data(user_key: str | None = None) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    counts = delete_learning_data(resolved_user_key)
    return WorkflowResult(
        messages=[
            f"약점 학습 데이터를 초기화했어. memory {counts['memory_items']}개, weakness {counts['weakness_items']}개를 정리했어."
        ],
        status=get_status(resolved_user_key).status,
        data=counts,
    )


def get_weakness_learning(user_key: str | None = None, *, training: bool = False) -> WorkflowResult:
    resolved_user_key = user_key or default_user_key()
    items = get_weakness_items(resolved_user_key, limit=10)
    if not items:
        return WorkflowResult(
            messages=["아직 누적된 약점 학습 데이터가 없어. 면접 최종 리뷰를 한 번 만들면 여기 쌓이기 시작해."],
            status=get_status(resolved_user_key).status,
            data={"weaknesses": [], "training": []},
        )

    top_items = items[:5]
    lines = []
    for index, item in enumerate(top_items, start=1):
        lines.append(
            f"{index}. {item['topic']} ({item['category']}, {item['weakness_type']}) "
            f"- 반복 {item['occurrence_count']}회, 심각도 {item['severity']}/5"
        )

    training_lines = [
        f"{index}. {item['suggested_training']}"
        for index, item in enumerate(top_items[:3], start=1)
        if item.get("suggested_training")
    ]
    message = "누적 약점은 이 순서로 보면 좋아.\n\n" + "\n".join(lines)
    if training or training_lines:
        message += "\n\n다음 연습 추천\n" + "\n".join(training_lines)

    return WorkflowResult(
        messages=[message],
        status=get_status(resolved_user_key).status,
        data={"weaknesses": items, "training": training_lines},
    )
