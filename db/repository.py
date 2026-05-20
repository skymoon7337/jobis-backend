import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError

from db.models import (
    AgentActionRecord,
    AgentChatMessageRecord,
    AgentPendingCommandRecord,
    AnalysisJobRecord,
    GithubProjectRecord,
    GithubSnapshotRecord,
    InterviewQuestionRecord,
    InterviewSession,
    InterviewTurnRecord,
    JobPostingRecord,
    MemoryItemRecord,
    User,
    WeaknessItemRecord,
)
from db.session import SessionLocal
from services.session import InterviewTurn, UserSession


TEST_USER_KEY_PREFIX = "test-agent-"
SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization|bearer|gemini[_-]?api[_-]?key)"
    r"(\s*[:=]\s*|\s+)[^\s,;]{8,}"
)
SENSITIVE_METADATA_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "gemini_api_key",
    "password",
    "secret",
    "token",
}


def is_test_user_key(user_key: str) -> bool:
    return user_key.strip().startswith(TEST_USER_KEY_PREFIX)


def redact_sensitive_text(value: str) -> str:
    return SENSITIVE_TEXT_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}[redacted]", value)


def redact_sensitive_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower().replace("-", "_")
            if normalized_key in SENSITIVE_METADATA_KEYS:
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = redact_sensitive_value(item)
        return redacted
    return value


def get_or_create_user(user_key: str) -> User:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if user:
            return user

        user = User(user_key=user_key)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            user = db.scalar(select(User).where(User.user_key == user_key))
            if user:
                return user
            raise
        db.refresh(user)
        return user


def load_user_session(user_key: str) -> UserSession:
    user = get_or_create_user(user_key)
    return UserSession(
        profile=user.profile or "",
        resume=user.resume or "",
        github_url=user.github_url or "",
        github_summary=user.github_summary or "",
        job_posting=user.job_posting or "",
    )


def load_context_data(user_key: str) -> dict[str, Any]:
    user = get_or_create_user(user_key)
    profile_updated_at = user.profile_updated_at or (user.updated_at if user.profile else None)
    resume_updated_at = user.resume_updated_at or (user.updated_at if user.resume else None)
    return {
        "profile": user.profile or "",
        "resume": user.resume or "",
        "profile_updated_at": profile_updated_at,
        "resume_updated_at": resume_updated_at,
        "github_url": user.github_url or "",
        "github_summary": user.github_summary or "",
        "job_posting": user.job_posting or "",
    }


def update_user_fields(user_key: str, **fields: Any) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            user = User(user_key=user_key)
            db.add(user)

        for key, value in fields.items():
            setattr(user, key, value)

        db.commit()


def delete_user_data(user_key: str, *, allow_non_test: bool = False) -> bool:
    if not allow_non_test and not is_test_user_key(user_key):
        raise ValueError(
            f"delete_user_data is limited to {TEST_USER_KEY_PREFIX}* users. "
            "Pass allow_non_test=True only for explicit maintenance tasks."
        )

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            return False

        session_ids = db.scalars(
            select(InterviewSession.id).where(InterviewSession.user_id == user.id)
        ).all()
        project_ids = db.scalars(
            select(GithubProjectRecord.id).where(GithubProjectRecord.user_id == user.id)
        ).all()

        if session_ids:
            db.query(InterviewTurnRecord).filter(InterviewTurnRecord.session_id.in_(session_ids)).delete(
                synchronize_session=False,
            )
            db.query(InterviewQuestionRecord).filter(InterviewQuestionRecord.session_id.in_(session_ids)).delete(
                synchronize_session=False,
            )
        if project_ids:
            db.query(GithubSnapshotRecord).filter(GithubSnapshotRecord.project_id.in_(project_ids)).delete(
                synchronize_session=False,
            )

        db.query(InterviewSession).filter(InterviewSession.user_id == user.id).delete(synchronize_session=False)
        db.query(AgentPendingCommandRecord).filter(AgentPendingCommandRecord.user_id == user.id).delete(
            synchronize_session=False,
        )
        db.query(AgentActionRecord).filter(AgentActionRecord.user_id == user.id).delete(synchronize_session=False)
        db.query(AgentChatMessageRecord).filter(AgentChatMessageRecord.user_id == user.id).delete(
            synchronize_session=False,
        )
        db.query(MemoryItemRecord).filter(MemoryItemRecord.user_id == user.id).delete(synchronize_session=False)
        db.query(WeaknessItemRecord).filter(WeaknessItemRecord.user_id == user.id).delete(synchronize_session=False)
        db.query(AnalysisJobRecord).filter(AnalysisJobRecord.user_id == user.id).delete(synchronize_session=False)
        db.query(JobPostingRecord).filter(JobPostingRecord.user_id == user.id).delete(synchronize_session=False)
        db.query(GithubProjectRecord).filter(GithubProjectRecord.user_id == user.id).delete(synchronize_session=False)
        db.delete(user)
        db.commit()
        return True


def save_agent_chat_message(
    user_key: str,
    *,
    role: str,
    content: str,
    action: str = "",
) -> dict[str, Any]:
    cleaned_content = content.strip()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            user = User(user_key=user_key)
            db.add(user)
            db.flush()

        message = AgentChatMessageRecord(
            user_id=user.id,
            role=role.strip()[:30],
            content=cleaned_content,
            action=action.strip()[:80],
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        return serialize_agent_chat_message(message)


def get_agent_chat_messages(user_key: str, limit: int = 50) -> list[dict[str, Any]]:
    bounded_limit = min(200, max(1, limit))
    with SessionLocal() as db:
        messages = db.scalars(
            select(AgentChatMessageRecord)
            .join(User, AgentChatMessageRecord.user_id == User.id)
            .where(User.user_key == user_key)
            .order_by(desc(AgentChatMessageRecord.created_at), desc(AgentChatMessageRecord.id))
            .limit(bounded_limit)
        ).all()
        return [serialize_agent_chat_message(message) for message in reversed(messages)]


def serialize_agent_chat_message(message: AgentChatMessageRecord) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role or "",
        "content": message.content or "",
        "action": message.action or "",
        "created_at": message.created_at,
    }


def save_agent_action(
    user_key: str,
    *,
    action: str,
    status: str,
    result_summary: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            user = User(user_key=user_key)
            db.add(user)
            db.flush()

        record = AgentActionRecord(
            user_id=user.id,
            action=action.strip()[:80],
            status=status.strip()[:30],
            result_summary=redact_sensitive_text(result_summary.strip())[:2000],
            metadata_json=json.dumps(redact_sensitive_value(metadata or {}), ensure_ascii=False, default=str),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return serialize_agent_action(record)


def get_agent_actions(user_key: str, limit: int = 50) -> list[dict[str, Any]]:
    bounded_limit = min(200, max(1, limit))
    with SessionLocal() as db:
        actions = db.scalars(
            select(AgentActionRecord)
            .join(User, AgentActionRecord.user_id == User.id)
            .where(User.user_key == user_key)
            .order_by(desc(AgentActionRecord.created_at), desc(AgentActionRecord.id))
            .limit(bounded_limit)
        ).all()
        return [serialize_agent_action(action) for action in reversed(actions)]


def serialize_agent_action(action: AgentActionRecord) -> dict[str, Any]:
    return {
        "id": action.id,
        "action": action.action or "",
        "status": action.status or "",
        "result_summary": redact_sensitive_text(action.result_summary or ""),
        "metadata": redact_sensitive_value(parse_json_object(action.metadata_json)),
        "created_at": action.created_at,
    }


def create_agent_pending_command(
    user_key: str,
    *,
    wait_job_id: int,
    command: str,
) -> dict[str, Any]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            user = User(user_key=user_key)
            db.add(user)
            db.flush()

        pending = AgentPendingCommandRecord(
            user_id=user.id,
            wait_job_id=wait_job_id,
            command=command.strip(),
            status="pending",
        )
        db.add(pending)
        db.commit()
        db.refresh(pending)
        return serialize_agent_pending_command(pending)


def get_agent_pending_commands(user_key: str, limit: int = 50) -> list[dict[str, Any]]:
    bounded_limit = min(200, max(1, limit))
    with SessionLocal() as db:
        pending_commands = db.scalars(
            select(AgentPendingCommandRecord)
            .join(User, AgentPendingCommandRecord.user_id == User.id)
            .where(User.user_key == user_key)
            .order_by(desc(AgentPendingCommandRecord.created_at), desc(AgentPendingCommandRecord.id))
            .limit(bounded_limit)
        ).all()
        return [serialize_agent_pending_command(pending) for pending in reversed(pending_commands)]


def claim_agent_pending_commands(user_key: str, wait_job_id: int) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        pending_commands = db.scalars(
            select(AgentPendingCommandRecord)
            .join(User, AgentPendingCommandRecord.user_id == User.id)
            .where(User.user_key == user_key)
            .where(AgentPendingCommandRecord.wait_job_id == wait_job_id)
            .where(AgentPendingCommandRecord.status == "pending")
            .order_by(AgentPendingCommandRecord.created_at, AgentPendingCommandRecord.id)
        ).all()
        claimed: list[dict[str, Any]] = []
        for pending in pending_commands:
            pending.status = "running"
            claimed.append(serialize_agent_pending_command(pending))
        db.commit()
        return claimed


def complete_agent_pending_command(
    pending_id: int,
    *,
    status: str,
    result_summary: str = "",
) -> dict[str, Any] | None:
    with SessionLocal() as db:
        pending = db.get(AgentPendingCommandRecord, pending_id)
        if not pending:
            return None
        pending.status = status.strip()[:30]
        pending.result_summary = redact_sensitive_text(result_summary.strip())[:2000]
        pending.finished_at = datetime.now(UTC)
        db.commit()
        db.refresh(pending)
        return serialize_agent_pending_command(pending)


def serialize_agent_pending_command(pending: AgentPendingCommandRecord) -> dict[str, Any]:
    return {
        "id": pending.id,
        "wait_job_id": pending.wait_job_id,
        "command": pending.command or "",
        "status": pending.status or "",
        "result_summary": redact_sensitive_text(pending.result_summary or ""),
        "created_at": pending.created_at,
        "updated_at": pending.updated_at,
        "finished_at": pending.finished_at,
    }


def normalize_weakness_topic(topic: str) -> str:
    normalized = re.sub(r"\s+", " ", topic.strip().lower())
    normalized = re.sub(r"[^0-9a-z가-힣+#.\s/-]", "", normalized)
    return normalized[:220]


def parse_json_int_list(value: str) -> list[int]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    result: list[int] = []
    for item in parsed:
        if isinstance(item, int) and item not in result:
            result.append(item)
    return result


def upsert_weakness_item(
    user_key: str,
    *,
    topic: str,
    category: str,
    weakness_type: str,
    severity: int,
    confidence: int,
    evidence: str,
    suggested_training: str,
    source_session_id: int | None = None,
    source_analysis_job_id: int | None = None,
) -> dict[str, Any]:
    cleaned_topic = topic.strip()[:200] or "면접 답변 보완"
    normalized_topic = normalize_weakness_topic(cleaned_topic)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            user = User(user_key=user_key)
            db.add(user)
            db.flush()

        item = db.scalar(
            select(WeaknessItemRecord)
            .where(WeaknessItemRecord.user_id == user.id)
            .where(WeaknessItemRecord.normalized_topic == normalized_topic)
            .where(WeaknessItemRecord.category == category.strip()[:80])
            .limit(1)
        )
        source_session_ids = [source_session_id] if source_session_id else []
        source_analysis_job_ids = [source_analysis_job_id] if source_analysis_job_id else []
        if item:
            existing_session_ids = parse_json_int_list(item.source_session_ids_json)
            existing_job_ids = parse_json_int_list(item.source_analysis_job_ids_json)
            for session_id in source_session_ids:
                if session_id not in existing_session_ids:
                    existing_session_ids.append(session_id)
            for job_id in source_analysis_job_ids:
                if job_id not in existing_job_ids:
                    existing_job_ids.append(job_id)

            item.topic = cleaned_topic
            item.weakness_type = weakness_type.strip()[:80] or item.weakness_type
            item.severity = max(1, min(5, max(item.severity or 1, severity)))
            item.confidence = max(1, min(5, max(item.confidence or 1, confidence) + 1))
            item.evidence = evidence.strip()[:2000] or item.evidence
            item.suggested_training = suggested_training.strip()[:2000] or item.suggested_training
            item.source_session_ids_json = json.dumps(existing_session_ids, ensure_ascii=False)
            item.source_analysis_job_ids_json = json.dumps(existing_job_ids, ensure_ascii=False)
            item.occurrence_count = (item.occurrence_count or 0) + 1
            item.last_seen_at = now
        else:
            item = WeaknessItemRecord(
                user_id=user.id,
                topic=cleaned_topic,
                normalized_topic=normalized_topic,
                category=category.strip()[:80],
                weakness_type=weakness_type.strip()[:80],
                severity=max(1, min(5, severity)),
                confidence=max(1, min(5, confidence)),
                evidence=evidence.strip()[:2000],
                suggested_training=suggested_training.strip()[:2000],
                source_session_ids_json=json.dumps(source_session_ids, ensure_ascii=False),
                source_analysis_job_ids_json=json.dumps(source_analysis_job_ids, ensure_ascii=False),
                occurrence_count=1,
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(item)

        db.commit()
        db.refresh(item)
        return serialize_weakness_item(item)


def get_weakness_items(user_key: str, limit: int = 10) -> list[dict[str, Any]]:
    bounded_limit = min(50, max(1, limit))
    with SessionLocal() as db:
        items = db.scalars(
            select(WeaknessItemRecord)
            .join(User, WeaknessItemRecord.user_id == User.id)
            .where(User.user_key == user_key)
            .order_by(
                desc(WeaknessItemRecord.confidence),
                desc(WeaknessItemRecord.severity),
                desc(WeaknessItemRecord.last_seen_at),
            )
            .limit(bounded_limit)
        ).all()
        return [serialize_weakness_item(item) for item in items]


def serialize_weakness_item(item: WeaknessItemRecord) -> dict[str, Any]:
    return {
        "id": item.id,
        "topic": item.topic or "",
        "category": item.category or "",
        "weakness_type": item.weakness_type or "",
        "severity": item.severity or 0,
        "confidence": item.confidence or 0,
        "evidence": item.evidence or "",
        "suggested_training": item.suggested_training or "",
        "source_session_ids": parse_json_int_list(item.source_session_ids_json),
        "source_analysis_job_ids": parse_json_int_list(item.source_analysis_job_ids_json),
        "occurrence_count": item.occurrence_count or 0,
        "first_seen_at": item.first_seen_at,
        "last_seen_at": item.last_seen_at,
    }


def upsert_memory_item(
    user_key: str,
    *,
    source_type: str,
    source_id: int,
    title: str,
    content: str,
    summary: str = "",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cleaned_source_type = source_type.strip()[:80]
    cleaned_source_id = max(0, int(source_id or 0))
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            user = User(user_key=user_key)
            db.add(user)
            db.flush()

        item = db.scalar(
            select(MemoryItemRecord)
            .where(MemoryItemRecord.user_id == user.id)
            .where(MemoryItemRecord.source_type == cleaned_source_type)
            .where(MemoryItemRecord.source_id == cleaned_source_id)
            .limit(1)
        )
        if item:
            item.title = title.strip()[:240]
            item.content = content.strip()
            item.summary = summary.strip()[:2000]
            item.tags_json = json.dumps(tags or [], ensure_ascii=False)
            item.metadata_json = json.dumps(redact_sensitive_value(metadata or {}), ensure_ascii=False, default=str)
        else:
            item = MemoryItemRecord(
                user_id=user.id,
                source_type=cleaned_source_type,
                source_id=cleaned_source_id,
                title=title.strip()[:240],
                content=content.strip(),
                summary=summary.strip()[:2000],
                tags_json=json.dumps(tags or [], ensure_ascii=False),
                metadata_json=json.dumps(redact_sensitive_value(metadata or {}), ensure_ascii=False, default=str),
            )
            db.add(item)

        db.commit()
        db.refresh(item)
        return serialize_memory_item(item)


def get_memory_items(user_key: str, limit: int = 20) -> list[dict[str, Any]]:
    bounded_limit = min(100, max(1, limit))
    with SessionLocal() as db:
        items = db.scalars(
            select(MemoryItemRecord)
            .join(User, MemoryItemRecord.user_id == User.id)
            .where(User.user_key == user_key)
            .order_by(desc(MemoryItemRecord.created_at), desc(MemoryItemRecord.id))
            .limit(bounded_limit)
        ).all()
        return [serialize_memory_item(item) for item in items]


def search_memory_items(user_key: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
    cleaned_query = query.strip()
    if not cleaned_query:
        return get_memory_items(user_key, limit=limit)

    keywords = [keyword for keyword in re.split(r"\s+", cleaned_query.lower()) if len(keyword) >= 2][:5]
    candidates = get_memory_items(user_key, limit=100)
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in candidates:
        haystack = "\n".join(
            str(item.get(key, ""))
            for key in ("title", "content", "summary", "source_type")
        ).lower()
        tag_text = " ".join(str(tag) for tag in item.get("tags", []))
        haystack = f"{haystack}\n{tag_text.lower()}"
        score = sum(haystack.count(keyword) for keyword in keywords)
        if score > 0:
            scored.append((score, item))

    return [
        item
        for _, item in sorted(scored, key=lambda scored_item: scored_item[0], reverse=True)[: max(1, limit)]
    ]


def delete_learning_data(user_key: str) -> dict[str, int]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            return {"memory_items": 0, "weakness_items": 0}

        memory_count = (
            db.query(MemoryItemRecord)
            .filter(MemoryItemRecord.user_id == user.id)
            .delete(synchronize_session=False)
        )
        weakness_count = (
            db.query(WeaknessItemRecord)
            .filter(WeaknessItemRecord.user_id == user.id)
            .delete(synchronize_session=False)
        )
        db.commit()
        return {"memory_items": int(memory_count or 0), "weakness_items": int(weakness_count or 0)}


def serialize_memory_item(item: MemoryItemRecord) -> dict[str, Any]:
    return {
        "id": item.id,
        "source_type": item.source_type or "",
        "source_id": item.source_id or 0,
        "title": item.title or "",
        "content": redact_sensitive_text(item.content or ""),
        "summary": redact_sensitive_text(item.summary or ""),
        "tags": [str(tag) for tag in parse_json_array(item.tags_json)],
        "metadata": redact_sensitive_value(parse_json_object(item.metadata_json)),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def create_job_posting(
    user_key: str,
    *,
    title: str,
    source_url: str,
    raw_text: str,
    summary: str,
) -> dict[str, Any]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            user = User(user_key=user_key)
            db.add(user)
            db.flush()

        old_selected = db.scalars(
            select(JobPostingRecord)
            .where(JobPostingRecord.user_id == user.id)
            .where(JobPostingRecord.is_selected.is_(True))
        ).all()
        for posting in old_selected:
            posting.is_selected = False

        posting = JobPostingRecord(
            user_id=user.id,
            title=title,
            source_url=source_url,
            raw_text=raw_text,
            summary=summary,
            is_selected=True,
        )
        user.job_posting = raw_text
        db.add(posting)
        db.commit()
        db.refresh(posting)
        postings = db.scalars(
            select(JobPostingRecord)
            .where(JobPostingRecord.user_id == user.id)
            .order_by(JobPostingRecord.id)
        ).all()
        index = next((position for position, item in enumerate(postings, start=1) if item.id == posting.id), None)
        return serialize_job_posting(posting, index=index)


def get_job_postings(user_key: str) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        postings = db.scalars(
            select(JobPostingRecord)
            .join(User, JobPostingRecord.user_id == User.id)
            .where(User.user_key == user_key)
            .order_by(JobPostingRecord.id)
        ).all()
        return [serialize_job_posting(posting, index=index) for index, posting in enumerate(postings, start=1)]


def get_selected_job_posting(user_key: str) -> dict[str, Any] | None:
    postings = get_job_postings(user_key)
    for posting in postings:
        if posting["is_selected"]:
            return posting
    return None


def get_job_posting_by_index(user_key: str, list_index: int) -> dict[str, Any] | None:
    postings = get_job_postings(user_key)
    if list_index < 1 or list_index > len(postings):
        return None
    return postings[list_index - 1]


def select_job_posting(user_key: str, list_index: int) -> dict[str, Any] | None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            return None

        postings = db.scalars(
            select(JobPostingRecord)
            .where(JobPostingRecord.user_id == user.id)
            .order_by(JobPostingRecord.id)
        ).all()
        if list_index < 1 or list_index > len(postings):
            return None

        selected = postings[list_index - 1]
        for posting in postings:
            posting.is_selected = posting.id == selected.id

        user.job_posting = selected.raw_text
        db.commit()
        db.refresh(selected)
        return serialize_job_posting(selected, index=list_index)


def delete_job_posting(user_key: str, list_index: int) -> tuple[dict[str, Any] | None, bool]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            return None, False

        postings = db.scalars(
            select(JobPostingRecord)
            .where(JobPostingRecord.user_id == user.id)
            .order_by(JobPostingRecord.id)
        ).all()
        if list_index < 1 or list_index > len(postings):
            return None, False

        posting = postings[list_index - 1]
        was_selected = bool(posting.is_selected)
        deleted = serialize_job_posting(posting, index=list_index)
        db.delete(posting)
        if was_selected:
            user.job_posting = ""
        db.commit()
        return deleted, was_selected


def serialize_job_posting(posting: JobPostingRecord, index: int | None = None) -> dict[str, Any]:
    display_name = posting.alias or posting.title or f"공고 {posting.id}"
    return {
        "id": posting.id,
        "index": index,
        "title": posting.title or f"공고 {posting.id}",
        "alias": posting.alias or "",
        "display_name": display_name,
        "source_url": posting.source_url or "",
        "raw_text": posting.raw_text or "",
        "summary": posting.summary or "",
        "is_selected": bool(posting.is_selected),
        "created_at": posting.created_at,
    }


def update_job_posting_alias(user_key: str, list_index: int, alias: str) -> dict[str, Any] | None:
    return update_job_posting_metadata(user_key, list_index, alias=alias)


def update_job_posting_metadata(
    user_key: str,
    list_index: int,
    *,
    alias: str | None = None,
    source_url: str | None = None,
) -> dict[str, Any] | None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            return None

        postings = db.scalars(
            select(JobPostingRecord)
            .where(JobPostingRecord.user_id == user.id)
            .order_by(JobPostingRecord.id)
        ).all()
        if list_index < 1 or list_index > len(postings):
            return None

        posting = postings[list_index - 1]
        if alias is not None:
            posting.alias = alias.strip()[:200]
        if source_url is not None:
            posting.source_url = source_url.strip()[:500]
        db.commit()
        db.refresh(posting)
        return serialize_job_posting(posting, index=list_index)


def update_job_posting_content(
    user_key: str,
    list_index: int,
    *,
    title: str,
    source_url: str,
    raw_text: str,
    summary: str,
) -> dict[str, Any] | None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            return None

        postings = db.scalars(
            select(JobPostingRecord)
            .where(JobPostingRecord.user_id == user.id)
            .order_by(JobPostingRecord.id)
        ).all()
        if list_index < 1 or list_index > len(postings):
            return None

        posting = postings[list_index - 1]
        posting.title = title
        posting.source_url = source_url
        posting.raw_text = raw_text
        posting.summary = summary
        if posting.is_selected:
            user.job_posting = raw_text
        db.commit()
        db.refresh(posting)
        return serialize_job_posting(posting, index=list_index)


def upsert_github_repository_snapshot(
    user_key: str,
    *,
    url: str,
    title: str,
    repo_key: str,
    summary: str,
    change_summary: str = "",
    default_branch: str = "",
    commit_sha: str = "",
    commit_date: datetime | None = None,
    alias: str = "",
) -> dict[str, Any]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            user = User(user_key=user_key)
            db.add(user)
            db.flush()

        project = db.scalar(
            select(GithubProjectRecord)
            .where(GithubProjectRecord.user_id == user.id)
            .where(GithubProjectRecord.repo_key == repo_key)
        )
        if not project:
            project = GithubProjectRecord(
                user_id=user.id,
                repo_key=repo_key,
                url=url,
                alias=alias,
                title=title,
            )
            db.add(project)
            db.flush()
        else:
            project.url = url
            project.title = title or project.title
            if alias:
                project.alias = alias

        latest_snapshot = db.scalar(
            select(GithubSnapshotRecord)
            .where(GithubSnapshotRecord.project_id == project.id)
            .where(GithubSnapshotRecord.is_latest.is_(True))
            .order_by(desc(GithubSnapshotRecord.version))
            .limit(1)
        )
        next_version = (latest_snapshot.version if latest_snapshot else 0) + 1
        if latest_snapshot:
            latest_snapshot.is_latest = False

        snapshot = GithubSnapshotRecord(
            project_id=project.id,
            version=next_version,
            summary=summary,
            change_summary=change_summary,
            default_branch=default_branch,
            commit_sha=commit_sha,
            commit_date=commit_date,
            analyzed_at=datetime.now(UTC),
            is_latest=True,
        )
        user.github_url = url
        user.github_summary = summary
        db.add(snapshot)
        db.commit()
        db.refresh(project)
        db.refresh(snapshot)
        projects = db.scalars(
            select(GithubProjectRecord)
            .where(GithubProjectRecord.user_id == user.id)
            .order_by(GithubProjectRecord.id)
        ).all()
        index = next((position for position, item in enumerate(projects, start=1) if item.id == project.id), None)
        return serialize_github_project(project, snapshot=snapshot, index=index)


def get_github_repositories(user_key: str) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            return []

        projects = db.scalars(
            select(GithubProjectRecord)
            .where(GithubProjectRecord.user_id == user.id)
            .order_by(GithubProjectRecord.id)
        ).all()
        if not projects and user.github_summary:
            title = github_repository_title(user.github_url or "")
            project = GithubProjectRecord(
                user_id=user.id,
                repo_key=title,
                url=user.github_url or "",
                title=title,
            )
            db.add(project)
            db.flush()
            db.add(
                GithubSnapshotRecord(
                    project_id=project.id,
                    version=1,
                    summary=user.github_summary or "",
                    change_summary="기존 GitHub 분석을 스냅샷으로 복구했습니다.",
                    analyzed_at=datetime.now(UTC),
                    is_latest=True,
                )
            )
            db.commit()
            projects = [project]

        return [
            serialize_github_project(project, snapshot=get_latest_github_snapshot(db, project.id), index=index)
            for index, project in enumerate(projects, start=1)
        ]


def get_github_repositories_by_indices(user_key: str, list_indices: list[int]) -> list[dict[str, Any]]:
    repositories = get_github_repositories(user_key)
    selected: list[dict[str, Any]] = []
    for list_index in list_indices:
        if list_index < 1 or list_index > len(repositories):
            continue
        selected.append(repositories[list_index - 1])
    return selected


def get_github_repository_by_repo_key(user_key: str, repo_key: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        project = db.scalar(
            select(GithubProjectRecord)
            .join(User, GithubProjectRecord.user_id == User.id)
            .where(User.user_key == user_key)
            .where(GithubProjectRecord.repo_key == repo_key)
        )
        if not project:
            return None

        projects = db.scalars(
            select(GithubProjectRecord)
            .where(GithubProjectRecord.user_id == project.user_id)
            .order_by(GithubProjectRecord.id)
        ).all()
        index = next((position for position, item in enumerate(projects, start=1) if item.id == project.id), None)
        return serialize_github_project(project, snapshot=get_latest_github_snapshot(db, project.id), index=index)


def update_github_repository_alias(user_key: str, list_index: int, alias: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            return None

        projects = db.scalars(
            select(GithubProjectRecord)
            .where(GithubProjectRecord.user_id == user.id)
            .order_by(GithubProjectRecord.id)
        ).all()
        if list_index < 1 or list_index > len(projects):
            return None

        project = projects[list_index - 1]
        project.alias = alias.strip()[:200]
        db.commit()
        db.refresh(project)
        return serialize_github_project(project, snapshot=get_latest_github_snapshot(db, project.id), index=list_index)


def delete_github_repository(user_key: str, list_index: int) -> dict[str, Any] | None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            return None

        projects = db.scalars(
            select(GithubProjectRecord)
            .where(GithubProjectRecord.user_id == user.id)
            .order_by(GithubProjectRecord.id)
        ).all()
        if list_index < 1 or list_index > len(projects):
            return None

        project = projects[list_index - 1]
        latest_snapshot = get_latest_github_snapshot(db, project.id)
        deleted = serialize_github_project(project, snapshot=latest_snapshot, index=list_index)
        db.delete(project)
        remaining = [item for item in projects if item.id != project.id]
        if user.github_url == project.url:
            latest_project = remaining[-1] if remaining else None
            latest = get_latest_github_snapshot(db, latest_project.id) if latest_project else None
            user.github_url = latest_project.url if latest_project else ""
            user.github_summary = latest.summary if latest else ""
        db.commit()
        return deleted


ACTIVE_JOB_STATUSES = {"queued", "running"}


def create_analysis_job(
    user_key: str,
    *,
    kind: str,
    input_data: dict[str, Any],
    stage: str,
    message: str,
    progress_current: int,
    progress_total: int,
) -> dict[str, Any]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            user = User(user_key=user_key)
            db.add(user)
            db.flush()

        job = AnalysisJobRecord(
            user_id=user.id,
            kind=kind,
            status="queued",
            stage=stage,
            message=message,
            progress_current=progress_current,
            progress_total=progress_total,
            input_json=json.dumps(input_data, ensure_ascii=False, default=str),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return serialize_analysis_job(job)


def get_analysis_job(user_key: str, job_id: int) -> dict[str, Any] | None:
    with SessionLocal() as db:
        job = db.scalar(
            select(AnalysisJobRecord)
            .join(User, AnalysisJobRecord.user_id == User.id)
            .where(User.user_key == user_key)
            .where(AnalysisJobRecord.id == job_id)
        )
        return serialize_analysis_job(job) if job else None


def get_active_analysis_job(user_key: str, kind: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        job = db.scalar(
            select(AnalysisJobRecord)
            .join(User, AnalysisJobRecord.user_id == User.id)
            .where(User.user_key == user_key)
            .where(AnalysisJobRecord.kind == kind)
            .where(AnalysisJobRecord.status.in_(ACTIVE_JOB_STATUSES))
            .order_by(desc(AnalysisJobRecord.created_at))
            .limit(1)
        )
        return serialize_analysis_job(job) if job else None


def get_latest_completed_analysis_job(user_key: str, kind: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        job = db.scalar(
            select(AnalysisJobRecord)
            .join(User, AnalysisJobRecord.user_id == User.id)
            .where(User.user_key == user_key)
            .where(AnalysisJobRecord.kind == kind)
            .where(AnalysisJobRecord.status == "completed")
            .order_by(desc(AnalysisJobRecord.finished_at), desc(AnalysisJobRecord.created_at))
            .limit(1)
        )
        return serialize_analysis_job(job) if job else None


def get_latest_completed_analysis_job_by_input(
    user_key: str,
    kind: str,
    input_data: dict[str, Any],
) -> dict[str, Any] | None:
    with SessionLocal() as db:
        jobs = db.scalars(
            select(AnalysisJobRecord)
            .join(User, AnalysisJobRecord.user_id == User.id)
            .where(User.user_key == user_key)
            .where(AnalysisJobRecord.kind == kind)
            .where(AnalysisJobRecord.status == "completed")
            .order_by(desc(AnalysisJobRecord.finished_at), desc(AnalysisJobRecord.created_at))
            .limit(20)
        ).all()

        for job in jobs:
            if parse_json_object(job.input_json) == input_data:
                return serialize_analysis_job(job)
        return None


def get_active_analysis_jobs(user_key: str) -> dict[str, dict[str, Any]]:
    with SessionLocal() as db:
        jobs = db.scalars(
            select(AnalysisJobRecord)
            .join(User, AnalysisJobRecord.user_id == User.id)
            .where(User.user_key == user_key)
            .where(AnalysisJobRecord.status.in_(ACTIVE_JOB_STATUSES))
            .order_by(desc(AnalysisJobRecord.created_at))
        ).all()

        latest_by_kind: dict[str, dict[str, Any]] = {}
        for job in jobs:
            if job.kind not in latest_by_kind:
                latest_by_kind[job.kind] = serialize_analysis_job(job)
        return latest_by_kind


def update_analysis_job(
    job_id: int,
    *,
    status: str | None = None,
    stage: str | None = None,
    message: str | None = None,
    progress_current: int | None = None,
    progress_total: int | None = None,
    result_data: dict[str, Any] | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    finished: bool = False,
) -> dict[str, Any] | None:
    with SessionLocal() as db:
        job = db.get(AnalysisJobRecord, job_id)
        if not job:
            return None

        if status is not None:
            job.status = status
        if stage is not None:
            job.stage = stage
        if message is not None:
            job.message = message
        if progress_current is not None:
            job.progress_current = progress_current
        if progress_total is not None:
            job.progress_total = progress_total
        if result_data is not None:
            job.result_json = json.dumps(result_data, ensure_ascii=False, default=str)
        if error_type is not None:
            job.error_type = error_type
        if error_message is not None:
            job.error_message = error_message
        if finished:
            job.finished_at = datetime.now(UTC)

        db.commit()
        db.refresh(job)
        return serialize_analysis_job(job)


def serialize_analysis_job(job: AnalysisJobRecord) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind or "",
        "status": job.status or "",
        "stage": job.stage or "",
        "message": job.message or "",
        "progress_current": job.progress_current or 0,
        "progress_total": job.progress_total or 0,
        "input": parse_json_object(job.input_json),
        "result": parse_json_object(job.result_json),
        "error_type": job.error_type or "",
        "error_message": job.error_message or "",
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
    }


def parse_json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_json_array(value: str) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def get_latest_github_snapshot(db, project_id: int) -> GithubSnapshotRecord | None:
    return db.scalar(
        select(GithubSnapshotRecord)
        .where(GithubSnapshotRecord.project_id == project_id)
        .where(GithubSnapshotRecord.is_latest.is_(True))
        .order_by(desc(GithubSnapshotRecord.version))
        .limit(1)
    )


def serialize_github_project(
    project: GithubProjectRecord,
    *,
    snapshot: GithubSnapshotRecord | None,
    index: int | None = None,
) -> dict[str, Any]:
    display_name = project.alias or project.title or project.repo_key or f"GitHub 저장소 {project.id}"
    return {
        "id": project.id,
        "index": index,
        "project_id": project.id,
        "snapshot_id": snapshot.id if snapshot else None,
        "url": project.url or "",
        "repo_key": project.repo_key or "",
        "title": project.title or project.repo_key or f"GitHub 저장소 {project.id}",
        "alias": project.alias or "",
        "display_name": display_name,
        "summary": snapshot.summary if snapshot else "",
        "change_summary": snapshot.change_summary if snapshot else "",
        "version": snapshot.version if snapshot else 0,
        "default_branch": snapshot.default_branch if snapshot else "",
        "commit_sha": snapshot.commit_sha if snapshot else "",
        "commit_date": snapshot.commit_date if snapshot else None,
        "analyzed_at": snapshot.analyzed_at if snapshot else None,
        "is_latest": bool(snapshot.is_latest) if snapshot else False,
        "created_at": project.created_at,
    }


def github_repository_title(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    parts = [part for part in cleaned.split("/") if part]
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return cleaned or "GitHub 저장소"


def reset_user_context(user_key: str) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            return

        user.profile = ""
        user.resume = ""
        user.profile_updated_at = None
        user.resume_updated_at = None
        user.github_url = ""
        user.github_summary = ""
        user.job_posting = ""

        postings = db.scalars(select(JobPostingRecord).where(JobPostingRecord.user_id == user.id)).all()
        for posting in postings:
            db.delete(posting)

        projects = db.scalars(select(GithubProjectRecord).where(GithubProjectRecord.user_id == user.id)).all()
        for project in projects:
            db.delete(project)

        db.commit()


def create_interview_session(user_key: str, context_snapshot: dict[str, Any] | None = None) -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_key == user_key))
        if not user:
            user = User(user_key=user_key)
            db.add(user)
            db.flush()

        old_active_sessions = db.scalars(
            select(InterviewSession)
            .where(InterviewSession.user_id == user.id)
            .where(InterviewSession.status == "active")
        ).all()
        for old_session in old_active_sessions:
            old_session.status = "stopped"
            old_session.current_display_id = ""
            old_session.awaiting_choice = False

        snapshot = context_snapshot or {}
        interview_session = InterviewSession(
            user_id=user.id,
            status="active",
            context_profile=snapshot.get("profile", ""),
            context_resume=snapshot.get("resume", ""),
            context_job_title=snapshot.get("job_title", ""),
            context_job_summary=snapshot.get("job_summary", ""),
            context_github_repositories=json.dumps(
                snapshot.get("github_repositories", []),
                ensure_ascii=False,
                default=str,
            ),
        )
        db.add(interview_session)
        db.commit()
        db.refresh(interview_session)
        return interview_session.id


def parse_json_list(value: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def serialize_interview_question(question: InterviewQuestionRecord) -> dict[str, Any]:
    return {
        "display_id": question.display_id or "",
        "question_type": question.question_type or "",
        "question": question.question or "",
        "is_bonus": bool(question.is_bonus),
        "bonus_type": question.bonus_type or "",
    }


def serialize_interview_turn(turn: InterviewTurnRecord) -> dict[str, Any]:
    return {
        "display_id": turn.display_id or "",
        "question_type": turn.question_type or "",
        "question": turn.question or "",
        "answer": turn.answer or "",
        "feedback": turn.feedback or "",
        "is_bonus": bool(turn.is_bonus),
        "bonus_type": turn.bonus_type or "",
    }


def update_interview_session(
    session_id: int,
    *,
    status: str | None = None,
    current_display_id: str | None = None,
    awaiting_choice: bool | None = None,
    summary: str | None = None,
    weakness_summary: str | None = None,
) -> None:
    with SessionLocal() as db:
        interview_session = db.get(InterviewSession, session_id)
        if not interview_session:
            return

        if status is not None:
            interview_session.status = status
        if current_display_id is not None:
            interview_session.current_display_id = current_display_id
        if awaiting_choice is not None:
            interview_session.awaiting_choice = awaiting_choice
        if summary is not None:
            interview_session.summary = summary
        if weakness_summary is not None:
            interview_session.weakness_summary = weakness_summary

        db.commit()


def get_active_interview_snapshot(user_key: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        interview_session = db.scalar(
            select(InterviewSession)
            .join(User, InterviewSession.user_id == User.id)
            .where(User.user_key == user_key)
            .where(InterviewSession.status == "active")
            .order_by(desc(InterviewSession.created_at))
            .limit(1)
        )
        if not interview_session:
            return None

        questions = db.scalars(
            select(InterviewQuestionRecord)
            .where(InterviewQuestionRecord.session_id == interview_session.id)
            .order_by(InterviewQuestionRecord.id)
        ).all()
        turns = db.scalars(
            select(InterviewTurnRecord)
            .where(InterviewTurnRecord.session_id == interview_session.id)
            .order_by(InterviewTurnRecord.id)
        ).all()

        return {
            "id": interview_session.id,
            "current_display_id": interview_session.current_display_id or "",
            "awaiting_choice": bool(interview_session.awaiting_choice),
            "questions": [serialize_interview_question(question) for question in questions],
            "turns": [serialize_interview_turn(turn) for turn in turns],
        }


def save_interview_turn(
    session_id: int,
    *,
    question_type: str,
    question: str,
    answer: str,
    feedback: str,
    display_id: str = "",
    is_bonus: bool = False,
    bonus_type: str = "",
) -> dict[str, Any]:
    with SessionLocal() as db:
        turn = InterviewTurnRecord(
            session_id=session_id,
            display_id=display_id,
            question_type=question_type,
            question=question,
            answer=answer,
            feedback=feedback,
            is_bonus=is_bonus,
            bonus_type=bonus_type,
        )
        db.add(turn)
        db.commit()
        db.refresh(turn)
        return serialize_interview_turn(turn)


def save_interview_question(
    session_id: int,
    *,
    display_id: str,
    question_type: str,
    question: str,
    is_bonus: bool = False,
    bonus_type: str = "",
) -> None:
    with SessionLocal() as db:
        db.add(
            InterviewQuestionRecord(
                session_id=session_id,
                display_id=display_id,
                question_type=question_type,
                question=question,
                is_bonus=is_bonus,
                bonus_type=bonus_type,
            )
        )
        db.commit()


def save_interview_questions(session_id: int, questions: list[dict[str, Any]]) -> None:
    with SessionLocal() as db:
        db.add_all(
            InterviewQuestionRecord(
                session_id=session_id,
                display_id=item.get("display_id", ""),
                question_type=item.get("question_type", ""),
                question=item.get("question", ""),
                is_bonus=item.get("is_bonus", False),
                bonus_type=item.get("bonus_type", ""),
            )
            for item in questions
        )
        db.commit()


def get_recent_sessions(user_key: str, limit: int = 5) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        sessions = db.scalars(
            select(InterviewSession)
            .join(User, InterviewSession.user_id == User.id)
            .where(User.user_key == user_key)
            .order_by(desc(InterviewSession.created_at))
            .limit(limit)
        ).all()

        result: list[dict[str, Any]] = []
        for session in sessions:
            questions = db.scalars(
                select(InterviewQuestionRecord)
                .where(InterviewQuestionRecord.session_id == session.id)
                .order_by(InterviewQuestionRecord.id)
            ).all()
            turns = db.scalars(
                select(InterviewTurnRecord)
                .where(InterviewTurnRecord.session_id == session.id)
                .order_by(InterviewTurnRecord.id)
            ).all()
            regular_questions = [question for question in questions if not question.is_bonus]
            result.append(
                {
                    "id": session.id,
                    "status": session.status,
                    "summary": session.summary or "",
                    "weakness_summary": session.weakness_summary or "",
                    "created_at": session.created_at,
                    "turn_count": sum(1 for turn in turns if not turn.is_bonus),
                    "question_count": len(regular_questions),
                    "job_title": session.context_job_title or "",
                    "job_summary": session.context_job_summary or "",
                    "github_repositories": parse_json_list(session.context_github_repositories),
                    "questions": [serialize_interview_question(question) for question in questions],
                    "turns": [serialize_interview_turn(turn) for turn in turns],
                }
            )

        return result


def get_latest_weakness_summary(user_key: str) -> str:
    with SessionLocal() as db:
        row = db.execute(
            select(InterviewSession.weakness_summary)
            .join(User, InterviewSession.user_id == User.id)
            .where(User.user_key == user_key)
            .where(InterviewSession.weakness_summary != "")
            .order_by(desc(InterviewSession.created_at))
            .limit(1)
        ).scalar_one_or_none()

    return row or ""


def get_latest_feedback_summary(user_key: str) -> str:
    with SessionLocal() as db:
        row = db.execute(
            select(InterviewSession.summary)
            .join(User, InterviewSession.user_id == User.id)
            .where(User.user_key == user_key)
            .where(InterviewSession.status == "completed")
            .where(InterviewSession.summary != "")
            .order_by(desc(InterviewSession.created_at))
            .limit(1)
        ).scalar_one_or_none()

    return row or ""


def get_recent_turns(user_key: str, limit: int = 20) -> list[InterviewTurn]:
    with SessionLocal() as db:
        rows = db.execute(
            select(InterviewTurnRecord)
            .join(InterviewSession, InterviewTurnRecord.session_id == InterviewSession.id)
            .join(User, InterviewSession.user_id == User.id)
            .where(User.user_key == user_key)
            .order_by(desc(InterviewTurnRecord.created_at))
            .limit(limit)
        ).scalars()

        return [
            InterviewTurn(
                question=turn.question,
                answer=turn.answer,
                feedback=turn.feedback,
                question_type=turn.question_type,
                display_id=turn.display_id,
                is_bonus=turn.is_bonus,
                bonus_type=turn.bonus_type,
            )
            for turn in rows
        ]
