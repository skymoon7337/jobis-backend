import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from db.repository import (
    create_analysis_job,
    create_interview_session,
    create_job_posting,
    delete_user_data,
    get_agent_actions,
    get_memory_items,
    get_weakness_items,
    save_agent_action,
    save_interview_question,
    save_interview_turn,
    update_analysis_job,
    update_interview_session,
    update_user_fields,
    upsert_github_repository_snapshot,
)
from main import app
from services.workflow_common import WorkflowResult
from services.workflow_weakness import record_weakness_learning_from_review


def test_user_key() -> str:
    return f"test-agent-{uuid4().hex}"


class AgentApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.user_key = test_user_key()

    def tearDown(self) -> None:
        delete_user_data(self.user_key)

    def post_agent(self, message: str) -> dict:
        response = self.client.post(
            "/api/agent",
            headers={"X-Jobis-User-Key": self.user_key},
            json={"message": message},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def get_agent_messages(self) -> dict:
        response = self.client.get(
            "/api/agent/messages?limit=10",
            headers={"X-Jobis-User-Key": self.user_key},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def get_agent_actions(self) -> dict:
        response = self.client.get(
            "/api/agent/actions?limit=10",
            headers={"X-Jobis-User-Key": self.user_key},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def get_agent_pending(self) -> dict:
        response = self.client.get(
            "/api/agent/pending?limit=10",
            headers={"X-Jobis-User-Key": self.user_key},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_agent_message_writes_chat_history_and_action_log(self) -> None:
        result = self.post_agent("공고 목록 보여줘")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "list_job_postings")

        messages = self.get_agent_messages()["data"]["messages"]
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["content"], "공고 목록 보여줘")
        self.assertEqual(messages[1]["action"], "list_job_postings")

        actions = self.get_agent_actions()["data"]["actions"]
        self.assertEqual(actions[-1]["action"], "list_job_postings")
        self.assertEqual(actions[-1]["status"], "completed")
        self.assertEqual(actions[-1]["metadata"]["intent_source"], "rule")

    def test_compound_command_runs_steps_and_logs_compound_action(self) -> None:
        create_job_posting(
            self.user_key,
            title="첫 공고",
            source_url="",
            raw_text="첫 공고 본문",
            summary="첫 공고 요약",
        )

        result = self.post_agent("1번 공고 선택하고 공고 목록 보여줘")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "compound")
        self.assertEqual(
            [step["action"] for step in result["data"]["steps"]],
            ["select_job_posting", "list_job_postings"],
        )

        actions = self.get_agent_actions()["data"]["actions"]
        self.assertEqual(
            [action["action"] for action in actions[-3:]],
            ["select_job_posting", "list_job_postings", "compound"],
        )
        self.assertEqual(actions[-1]["metadata"]["step_actions"], ["select_job_posting", "list_job_postings"])

    def test_background_compound_command_creates_pending_command(self) -> None:
        fake_job = {
            "id": 987654,
            "kind": "job_posting",
            "status": "queued",
            "stage": "queued",
            "message": "공고 분석을 준비하고 있습니다.",
            "progress_current": 0,
            "progress_total": 5,
            "input": {},
            "result": {},
            "error_type": "",
            "error_message": "",
            "created_at": "",
            "updated_at": "",
            "finished_at": None,
        }
        fake_result = WorkflowResult(
            messages=["공고 분석 작업을 시작했습니다."],
            status={
                "profile": False,
                "resume": False,
                "github": False,
                "job": False,
                "interview": "idle",
                "next_recommendation": "profile",
            },
            data={"job": fake_job},
        )

        with (
            patch("services.agent.create_job_posting_job", return_value=(fake_result, True)),
            patch("services.agent.run_job_posting_job", return_value=None),
        ):
            result = self.post_agent("공고 저장해줘 테스트 공고 본문입니다 하고 공고 목록 보여줘")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "compound")
        self.assertIn("자동으로 이어서 실행", result["data"]["stopped_reason"])

        pending_commands = self.get_agent_pending()["data"]["pending_commands"]
        self.assertEqual(pending_commands[-1]["wait_job_id"], fake_job["id"])
        self.assertEqual(pending_commands[-1]["command"], "공고 목록 보여줘")
        self.assertEqual(pending_commands[-1]["status"], "pending")

        actions = self.get_agent_actions()["data"]["actions"]
        self.assertEqual(actions[-1]["action"], "compound")
        self.assertEqual(actions[-1]["metadata"]["pending_wait_job_id"], fake_job["id"])
        self.assertEqual(actions[-1]["metadata"]["pending_command"], "공고 목록 보여줘")

    def test_pending_command_payload_hides_sensitive_save_command_text(self) -> None:
        fake_job = {
            "id": 987655,
            "kind": "job_posting",
            "status": "queued",
            "stage": "queued",
            "message": "공고 분석을 준비하고 있습니다.",
            "progress_current": 0,
            "progress_total": 5,
            "input": {},
            "result": {},
            "error_type": "",
            "error_message": "",
            "created_at": "",
            "updated_at": "",
            "finished_at": None,
        }
        fake_result = WorkflowResult(
            messages=["공고 분석 작업을 시작했습니다."],
            status={
                "profile": False,
                "resume": False,
                "github": False,
                "job": False,
                "interview": "idle",
                "next_recommendation": "profile",
            },
            data={"job": fake_job},
        )

        with (
            patch("services.agent.create_job_posting_job", return_value=(fake_result, True)),
            patch("services.agent.run_job_posting_job", return_value=None),
        ):
            self.post_agent("공고 저장해줘 테스트 공고 본문입니다 하고 자소서 저장해줘 민감한 자기소개서 원문")

        pending_commands = self.get_agent_pending()["data"]["pending_commands"]
        self.assertEqual(pending_commands[-1]["command"], "자소서 저장 명령")

        actions = self.get_agent_actions()["data"]["actions"]
        self.assertEqual(actions[-1]["metadata"]["pending_command"], "자소서 저장 명령")

    def test_agent_action_redacts_secret_like_metadata(self) -> None:
        save_agent_action(
            self.user_key,
            action="test",
            status="failed",
            result_summary="GEMINI_API_KEY=abcdef1234567890",
            metadata={"api_key": "abcdef1234567890", "detail": "token: abcdef1234567890"},
        )

        action = get_agent_actions(self.user_key)[-1]

        self.assertEqual(action["metadata"]["api_key"], "[redacted]")
        self.assertIn("[redacted]", action["metadata"]["detail"])
        self.assertIn("[redacted]", action["result_summary"])

    def test_question_plan_reuses_completed_matching_job(self) -> None:
        update_user_fields(self.user_key, resume="테스트 자소서")
        job = create_job_posting(
            self.user_key,
            title="테스트 공고",
            source_url="",
            raw_text="테스트 공고 본문",
            summary="테스트 공고 요약",
        )
        github = upsert_github_repository_snapshot(
            self.user_key,
            url="https://github.com/example/repo",
            title="example repo",
            repo_key="example/repo",
            summary="테스트 GitHub 요약",
        )
        input_data = {
            "job_index": 1,
            "job_id": job["id"],
            "github_indices": [1],
            "github_snapshot_ids": [github["snapshot_id"]],
            "question_counts": {
                "CS 기본기": 1,
                "언어": 0,
                "기술스택": 0,
                "프로젝트/GitHub": 0,
                "프로젝트/자소서": 0,
            },
            "question_plan": ["CS 기본기"],
        }
        cached_job = create_analysis_job(
            self.user_key,
            kind="question_plan",
            input_data=input_data,
            stage="completed",
            message="질문 후보 생성이 완료되었습니다.",
            progress_current=5,
            progress_total=5,
        )
        update_analysis_job(
            cached_job["id"],
            status="completed",
            stage="completed",
            result_data={
                "questions": [
                    {
                        "id": 1,
                        "question_type": "CS 기본기",
                        "question": "트랜잭션 격리 수준을 설명해주세요.",
                    }
                ]
            },
            finished=True,
        )

        with patch("services.agent.run_question_plan_job") as run_question_plan_job:
            result = self.post_agent("1번 공고랑 1번 GitHub로 질문 1개 만들어줘")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "create_question_plan")
        self.assertTrue(result["data"]["cached"])
        self.assertEqual(result["data"]["job"]["id"], cached_job["id"])
        run_question_plan_job.assert_not_called()

        actions = self.get_agent_actions()["data"]["actions"]
        self.assertEqual(actions[-1]["action"], "create_question_plan")
        self.assertEqual(actions[-1]["metadata"]["job_id"], cached_job["id"])

    def test_start_interview_continues_active_session(self) -> None:
        update_user_fields(self.user_key, resume="테스트 자소서")
        start_response = self.client.post(
            "/api/interview/start",
            headers={"X-Jobis-User-Key": self.user_key},
            json={
                "questions": [
                    {
                        "question_type": "CS 기본기",
                        "question": "트랜잭션 격리 수준을 설명해주세요.",
                    }
                ]
            },
        )
        self.assertEqual(start_response.status_code, 200, start_response.text)
        self.assertTrue(start_response.json()["data"]["active"])

        result = self.post_agent("면접 시작해줘")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "start_interview")
        self.assertTrue(result["data"]["active"])
        self.assertTrue(result["data"]["continued_existing"])
        self.assertIn("이미 진행 중인 면접", result["reply"])

    def test_interview_continuation_shows_current_question(self) -> None:
        update_user_fields(self.user_key, resume="테스트 자소서")
        start_response = self.client.post(
            "/api/interview/start",
            headers={"X-Jobis-User-Key": self.user_key},
            json={
                "questions": [
                    {
                        "question_type": "CS 기본기",
                        "question": "트랜잭션 격리 수준을 설명해주세요.",
                    }
                ]
            },
        )
        self.assertEqual(start_response.status_code, 200, start_response.text)

        result = self.post_agent("현재 질문 보여줘")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "get_interview")
        self.assertTrue(result["data"]["active"])
        self.assertTrue(result["data"]["continued_existing"])
        self.assertIn("트랜잭션 격리 수준", result["reply"])

    def test_delete_user_data_requires_test_user_key_by_default(self) -> None:
        with self.assertRaises(ValueError):
            delete_user_data("local")

    def test_records_weakness_learning_from_review(self) -> None:
        learned = record_weakness_learning_from_review(
            self.user_key,
            session_id=123,
            analysis_job_id=456,
            weakness_summary=(
                "트랜잭션 격리 수준: READ COMMITTED와 REPEATABLE READ 차이를 구체적으로 설명하지 못함\n"
                "답변 구조: 결론보다 배경 설명이 길어 핵심이 늦게 나옴"
            ),
        )

        self.assertEqual(len(learned), 2)
        items = get_weakness_items(self.user_key)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["source_session_ids"], [123])
        self.assertEqual(items[0]["source_analysis_job_ids"], [456])

        record_weakness_learning_from_review(
            self.user_key,
            session_id=789,
            analysis_job_id=999,
            weakness_summary="트랜잭션 격리 수준: phantom read 예시가 부족함",
        )
        updated_items = get_weakness_items(self.user_key)
        transaction_item = next(item for item in updated_items if item["topic"] == "트랜잭션 격리 수준")
        self.assertEqual(transaction_item["occurrence_count"], 2)
        self.assertIn(789, transaction_item["source_session_ids"])

    def test_agent_can_show_weaknesses_and_training_plan(self) -> None:
        record_weakness_learning_from_review(
            self.user_key,
            session_id=123,
            analysis_job_id=456,
            weakness_summary="트랜잭션 격리 수준: 개념 차이 설명이 부족함",
        )

        weakness_result = self.post_agent("내 약점 보여줘")

        self.assertTrue(weakness_result["ok"])
        self.assertEqual(weakness_result["action"], "get_weaknesses")
        self.assertIn("트랜잭션 격리 수준", weakness_result["reply"])

        training_result = self.post_agent("이번에 뭐 연습하면 돼?")

        self.assertTrue(training_result["ok"])
        self.assertEqual(training_result["action"], "get_training_plan")
        self.assertIn("다음 연습 추천", training_result["reply"])

    def test_interview_answer_creates_memory_item_and_agent_can_search_it(self) -> None:
        update_user_fields(self.user_key, resume="테스트 자소서")
        start_response = self.client.post(
            "/api/interview/start",
            headers={"X-Jobis-User-Key": self.user_key},
            json={
                "questions": [
                    {
                        "question_type": "CS 기본기",
                        "question": "트랜잭션 격리 수준을 설명해주세요.",
                    }
                ]
            },
        )
        self.assertEqual(start_response.status_code, 200, start_response.text)

        result = self.post_agent("내 답변은 READ COMMITTED와 REPEATABLE READ 차이를 설명했습니다.")

        self.assertTrue(result["ok"])
        memories = get_memory_items(self.user_key)
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["source_type"], "interview_turn")
        self.assertIn("트랜잭션 격리 수준", memories[0]["content"])

        search_result = self.post_agent("트랜잭션 피드백 찾아줘")

        self.assertTrue(search_result["ok"])
        self.assertEqual(search_result["action"], "search_memory")
        self.assertIn("트랜잭션", search_result["reply"])

    def test_agent_searches_interview_history_without_falling_back_to_latest_review(self) -> None:
        update_user_fields(self.user_key, resume="테스트 자소서")
        start_response = self.client.post(
            "/api/interview/start",
            headers={"X-Jobis-User-Key": self.user_key},
            json={
                "questions": [
                    {
                        "question_type": "기술스택",
                        "question": "웹소켓과 HTTP polling의 차이를 설명해주세요.",
                    }
                ]
            },
        )
        self.assertEqual(start_response.status_code, 200, start_response.text)

        result = self.post_agent("내가 했던 면접중에 웹소켓 관련 질문이 있었나?")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "search_interview_history")
        self.assertEqual(result["data"]["query"], "웹소켓")
        self.assertEqual(result["data"]["match_count"], 1)
        self.assertIn("웹소켓", result["reply"])

        action = self.get_agent_actions()["data"]["actions"][-1]
        self.assertEqual(action["action"], "search_interview_history")
        self.assertEqual(action["metadata"]["query"], "웹소켓")
        self.assertEqual(action["metadata"]["match_count"], 1)

    def test_interview_history_search_reports_empty_results(self) -> None:
        update_user_fields(self.user_key, resume="테스트 자소서")
        start_response = self.client.post(
            "/api/interview/start",
            headers={"X-Jobis-User-Key": self.user_key},
            json={
                "questions": [
                    {
                        "question_type": "CS 기본기",
                        "question": "트랜잭션 격리 수준을 설명해주세요.",
                    }
                ]
            },
        )
        self.assertEqual(start_response.status_code, 200, start_response.text)

        result = self.post_agent("내가 했던 면접중에 웹소켓 관련 질문이 있었나?")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "search_interview_history")
        self.assertEqual(result["data"]["match_count"], 0)
        self.assertIn("찾지 못했어", result["reply"])
        self.assertIn("임의 이동하지 않았고", result["reply"])

    def test_interview_history_search_merges_same_question_sources(self) -> None:
        session_id = create_interview_session(
            self.user_key,
            {
                "job_title": "토스 - Backend Engineer",
                "github_repositories": [],
            },
        )
        question = (
            "TransactionReportMapRepository에서 데이터를 메모리에 적재하여 집계하고 있는데, "
            "메모리 한계를 초과할 경우 어떤 대안을 적용하시겠습니까?"
        )
        save_interview_question(
            session_id,
            display_id="4-1",
            question_type="프로젝트/GitHub",
            question=question,
        )
        save_interview_turn(
            session_id,
            display_id="4-1",
            question_type="프로젝트/GitHub",
            question=question,
            answer="메모리 기반 Map 대신 chunk 단위 처리와 외부 저장소를 사용합니다.",
            feedback="메모리 한계 대응이 구체적입니다.",
        )
        update_interview_session(
            session_id,
            status="completed",
            summary=(
                "문항별 피드백\n"
                "[프로젝트/GitHub] ▶ 4-1 [메모리 한계 시 대안]\n"
                "질문: 메모리 누적 집계의 장애 방지 대안은?\n"
                "답변: chunk 단위 처리와 외부 저장소 활용\n"
                "평가: 메모리 한계 대응이 좋습니다."
            ),
        )

        result = self.post_agent("메모리 한계 관련 질문 있었나?")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "search_interview_history")
        self.assertEqual(result["data"]["match_count"], 1)
        match = result["data"]["matches"][0]
        self.assertEqual(match["session_id"], session_id)
        self.assertEqual(match["display_id"], "4-1")
        self.assertEqual(match["source_type"], "turn")
        self.assertCountEqual(match["sources"], ["질문", "답변/피드백", "최종 리뷰"])
        self.assertNotIn("토스 - Backend Engineer", match["excerpt"])
        self.assertIn("문항 1개", result["reply"])

    def test_interview_history_search_can_filter_answered_items_only(self) -> None:
        question_only_session_id = create_interview_session(
            self.user_key,
            {"job_title": "질문만 있는 세션", "github_repositories": []},
        )
        save_interview_question(
            question_only_session_id,
            display_id="4-1",
            question_type="프로젝트/GitHub",
            question="메모리 한계 상황에서 어떤 대안을 고려할 수 있습니까?",
        )
        answered_session_id = create_interview_session(
            self.user_key,
            {"job_title": "답변 있는 세션", "github_repositories": []},
        )
        question = "메모리 한계 상황에서 어떤 대안을 고려할 수 있습니까?"
        save_interview_question(
            answered_session_id,
            display_id="4-1",
            question_type="프로젝트/GitHub",
            question=question,
        )
        save_interview_turn(
            answered_session_id,
            display_id="4-1",
            question_type="프로젝트/GitHub",
            question=question,
            answer="chunk 단위 처리로 메모리 사용량을 제한합니다.",
            feedback="대안이 구체적입니다.",
        )

        result = self.post_agent("메모리 한계 답변 피드백 있는 최근 2주 면접 기록 찾아줘")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "search_interview_history")
        self.assertEqual(result["data"]["total_count"], 1)
        self.assertNotIn("있는", result["data"]["query"])
        self.assertEqual(result["data"]["matches"][0]["session_id"], answered_session_id)
        self.assertEqual(result["data"]["matches"][0]["source_type"], "turn")

    def test_interview_history_search_returns_representative_results_and_more_batch(self) -> None:
        for index in range(6):
            session_id = create_interview_session(
                self.user_key,
                {
                    "job_title": f"회사{index} - Backend Engineer",
                    "github_repositories": [{"title": f"프로젝트{index}"}],
                },
            )
            question = f"메모리 한계 상황에서 배치 처리 안정성을 어떻게 지키겠습니까? {index}"
            save_interview_question(
                session_id,
                display_id="4-1",
                question_type="프로젝트/GitHub",
                question=question,
            )
            save_interview_turn(
                session_id,
                display_id="4-1",
                question_type="프로젝트/GitHub",
                question=question,
                answer="메모리 한계가 오지 않도록 chunk 처리와 외부 저장소를 씁니다.",
                feedback="메모리 한계 대응을 설명했습니다.",
            )
            update_interview_session(session_id, status="completed")

        result = self.post_agent("메모리 한계 관련 질문 있었나?")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "search_interview_history")
        self.assertEqual(result["data"]["total_count"], 6)
        self.assertEqual(result["data"]["shown_count"], 4)
        self.assertEqual(result["data"]["match_count"], 4)
        self.assertTrue(result["data"]["has_more"])
        self.assertEqual(len(result["data"]["matches"]), 4)
        self.assertEqual(len(result["data"]["next_matches"]), 2)
        self.assertTrue(result["data"]["refine_suggestions"])
        self.assertIn("관련도가 높은 4개", result["reply"])

    def test_reset_learning_requires_confirmation_then_deletes_memory_and_weaknesses(self) -> None:
        record_weakness_learning_from_review(
            self.user_key,
            session_id=123,
            analysis_job_id=456,
            weakness_summary="트랜잭션 격리 수준: 개념 차이 설명이 부족함",
        )
        update_user_fields(self.user_key, resume="테스트 자소서")
        start_response = self.client.post(
            "/api/interview/start",
            headers={"X-Jobis-User-Key": self.user_key},
            json={
                "questions": [
                    {
                        "question_type": "CS 기본기",
                        "question": "트랜잭션 격리 수준을 설명해주세요.",
                    }
                ]
            },
        )
        self.assertEqual(start_response.status_code, 200, start_response.text)
        self.post_agent("내 답변은 격리 수준을 설명했습니다.")

        blocked = self.post_agent("약점 학습 초기화")

        self.assertFalse(blocked["ok"])
        self.assertTrue(get_memory_items(self.user_key))
        self.assertTrue(get_weakness_items(self.user_key))

        reset = self.post_agent("약점 학습 초기화 확인")

        self.assertTrue(reset["ok"])
        self.assertFalse(get_memory_items(self.user_key))
        self.assertFalse(get_weakness_items(self.user_key))


if __name__ == "__main__":
    unittest.main()
