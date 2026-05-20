from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env")

from db.repository import (  # noqa: E402
    create_job_posting,
    delete_user_data,
    update_user_fields,
    upsert_github_repository_snapshot,
)


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_USER_KEY = "test-agent-full-scenario"


PROFILE = (
    "백엔드 신입 개발자입니다. Java, Spring Boot, MySQL, Redis를 사용해 REST API와 "
    "트랜잭션이 중요한 예약/주문 흐름을 구현했습니다."
)
RESUME = (
    "동시 예약 요청에서 데이터 정합성이 깨지는 문제를 발견했고, 트랜잭션 범위와 "
    "비관적 락을 비교한 뒤 테스트 코드로 하나의 요청만 성공하도록 검증했습니다."
)
JOB_TEXT = (
    "회사: Jobis Bank\n"
    "직무: Backend Engineer\n"
    "주요업무: Spring Boot 기반 금융 API 개발, MySQL 트랜잭션 처리, 장애 추적 로그 개선\n"
    "필수역량: Java, Spring Boot, JPA, MySQL, REST API, 동시성 문제 해결 경험"
)
GITHUB_SUMMARY = (
    "Spring Boot 예약 서비스 프로젝트입니다. 예약 생성 API, 결제 승인 후 상태 전이, "
    "중복 예약 방지를 위한 비관적 락, 통합 테스트를 포함합니다."
)
ANSWER = (
    "동시 요청에서는 조회와 변경 사이에 다른 트랜잭션이 끼어들 수 있어서 "
    "예약 대상 row를 SELECT FOR UPDATE로 잠그고, 실패 요청은 명확한 예외로 처리했습니다."
)


def assert_ok(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def request_json(client: httpx.Client, method: str, path: str, **kwargs: object) -> dict[str, Any]:
    response = client.request(method, path, **kwargs)
    try:
        payload = response.json()
    except ValueError as exc:
        raise AssertionError(f"{method} {path} returned non-JSON response: {response.text}") from exc

    assert_ok(response.status_code == 200, f"{method} {path} returned {response.status_code}: {payload}")
    return payload


def post_agent(client: httpx.Client, message: str) -> dict[str, Any]:
    payload = request_json(client, "POST", "/api/agent", json={"message": message})
    assert_ok(payload.get("ok") is True, f"agent command failed for {message!r}: {payload}")
    return payload


def wait_for_job(client: httpx.Client, job_id: int, *, timeout_seconds: int = 180) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        payload = request_json(client, "GET", f"/api/analysis-jobs/{job_id}")
        job = payload.get("data", {}).get("job")
        if not isinstance(job, dict):
            raise AssertionError(f"job payload missing for {job_id}: {payload}")

        status = job.get("status")
        if status == "completed":
            return job
        if status in {"failed", "cancelled"}:
            raise AssertionError(f"job {job_id} failed: {job}")
        time.sleep(2)

    raise TimeoutError(f"job {job_id} did not finish within {timeout_seconds} seconds")


def seed_user_data(user_key: str) -> None:
    update_user_fields(user_key, profile=PROFILE, resume=RESUME)
    create_job_posting(
        user_key,
        title="Jobis Bank - Backend Engineer",
        source_url="https://example.com/jobis-bank-backend",
        raw_text=JOB_TEXT,
        summary=JOB_TEXT,
    )
    upsert_github_repository_snapshot(
        user_key,
        url="https://github.com/example/jobis-reservation",
        title="jobis-reservation",
        repo_key="example/jobis-reservation",
        summary=GITHUB_SUMMARY,
        change_summary="테스트용 스냅샷입니다.",
        default_branch="main",
        commit_sha="scenario-smoke",
    )


def run_scenario(base_url: str, user_key: str, keep_data: bool) -> None:
    if not keep_data:
        delete_user_data(user_key)
    seed_user_data(user_key)

    try:
        with httpx.Client(
            base_url=base_url,
            headers={"X-Jobis-User-Key": user_key},
            timeout=20.0,
        ) as client:
            question_plan = post_agent(client, "1번 공고랑 1번 GitHub로 질문 1개 만들어줘")
            assert_ok(question_plan.get("action") == "create_question_plan", f"unexpected action: {question_plan}")
            plan_job = question_plan.get("data", {}).get("job")
            assert_ok(isinstance(plan_job, dict) and plan_job.get("id"), f"missing question plan job: {question_plan}")
            completed_plan = wait_for_job(client, int(plan_job["id"]))
            candidates = completed_plan.get("result", {}).get("questions", [])
            assert_ok(len(candidates) >= 1, f"question candidates were not generated: {completed_plan}")

            interview = post_agent(client, "방금 만든 질문 후보로 면접 시작해줘")
            assert_ok(interview.get("action") == "start_interview", f"unexpected interview action: {interview}")
            assert_ok(interview.get("status", {}).get("interview") == "active", f"interview not started: {interview}")
            assert_ok(interview.get("data", {}).get("active") is True, f"interview payload not active: {interview}")

            answer = post_agent(client, f"내 답변은 {ANSWER}")
            assert_ok(answer.get("action") == "submit_interview_answer", f"unexpected answer action: {answer}")

            review = post_agent(client, "최종 리뷰 만들어줘")
            assert_ok(review.get("action") == "create_final_review", f"unexpected review action: {review}")
            review_job = review.get("data", {}).get("job")
            assert_ok(isinstance(review_job, dict) and review_job.get("id"), f"missing review job: {review}")
            completed_review = wait_for_job(client, int(review_job["id"]))
            assert_ok(
                completed_review.get("result", {}).get("review"),
                f"review payload was not saved: {completed_review}",
            )
            review_result = completed_review.get("result", {})
            assert_ok(
                review_result.get("learned_weaknesses") is not None,
                f"weakness learning result missing: {completed_review}",
            )
            assert_ok(
                review_result.get("review_memory") is not None,
                f"review memory result missing: {completed_review}",
            )

            latest_review = post_agent(client, "최근 리뷰 불러줘")
            assert_ok(latest_review.get("action") == "get_latest_review", f"unexpected latest review: {latest_review}")
            assert_ok(
                "weaknesses" in latest_review.get("data", {}),
                f"latest review did not include learned weaknesses: {latest_review}",
            )
            assert_ok(
                "memories" in latest_review.get("data", {}),
                f"latest review did not include memory items: {latest_review}",
            )

            weaknesses = post_agent(client, "내 약점 보여줘")
            assert_ok(weaknesses.get("action") == "get_weaknesses", f"unexpected weaknesses: {weaknesses}")

            memories = post_agent(client, "트랜잭션 피드백 찾아줘")
            assert_ok(memories.get("action") == "search_memory", f"unexpected memory search: {memories}")

        print("agent full scenario smoke passed")
        print(f"base_url={base_url}")
        print(f"user_key={user_key}")
    finally:
        if not keep_data:
            delete_user_data(user_key)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a full Jobis agent scenario against a running local API.")
    parser.add_argument(
        "--run-llm",
        action="store_true",
        help="Required. This scenario generates questions and a final review with the configured LLM.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("JOBIS_SMOKE_BASE_URL", DEFAULT_BASE_URL),
        help=f"Running backend base URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--user-key",
        default=os.getenv("JOBIS_FULL_SCENARIO_USER_KEY", DEFAULT_USER_KEY),
        help=f"Temporary user key for scenario data. Default: {DEFAULT_USER_KEY}",
    )
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Do not delete scenario user data before and after the run.",
    )
    args = parser.parse_args()

    if not args.run_llm:
        raise SystemExit("Refusing to run LLM scenario smoke without --run-llm.")

    run_scenario(args.base_url.rstrip("/"), args.user_key, args.keep_data)


if __name__ == "__main__":
    main()
