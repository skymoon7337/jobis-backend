import unittest

from services.agent import (
    QUESTION_TYPES,
    _extract_answer_text,
    _extract_github_indices,
    _extract_github_url,
    _extract_index,
    _extract_job_text,
    _extract_interview_history_query,
    _extract_profile_text,
    _extract_question_count,
    _extract_resume_text,
    _looks_like_interview_history_search,
    _looks_like_candidate_interview_start,
    _looks_like_question_plan,
    _question_counts_from_total,
    _split_compound_message,
)


class AgentRouterParsingTest(unittest.TestCase):
    def test_extracts_numeric_and_korean_ordinals(self) -> None:
        cases = {
            "1번 공고 선택해줘": 1,
            "2번째 공고로 면접 시작": 2,
            "첫 번째 공고 선택": 1,
            "두번째 공고로 면접": 2,
            "세 번째 공고 분석": 3,
            "공고 목록 보여줘": None,
        }

        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(_extract_index(message), expected)

    def test_extracts_github_indices(self) -> None:
        cases = {
            "1번 공고랑 2번 GitHub로 질문 5개 만들어줘": [2],
            "GitHub 1번과 github 2번으로 질문 만들어줘": [1, 2],
            "깃허브 프로젝트 3번으로 질문 생성": [3],
            "GitHub 분석해줘 https://github.com/a/b": [],
        }

        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(_extract_github_indices(message), expected)

    def test_extracts_question_count(self) -> None:
        cases = {
            "질문 5개 만들어줘": 5,
            "질문 후보 4개 생성": 4,
            "3개 면접 질문 생성": 3,
            "질문 9개 만들어줘": None,
            "질문 만들어줘": None,
        }

        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(_extract_question_count(message), expected)

    def test_distributes_question_counts(self) -> None:
        self.assertEqual(sum(_question_counts_from_total(5).values()), 5)
        self.assertEqual(sum(_question_counts_from_total(8).values()), 8)
        self.assertLessEqual(max(_question_counts_from_total(8).values()), 3)
        self.assertEqual(set(_question_counts_from_total(5)), set(QUESTION_TYPES))
        self.assertEqual(sum(_question_counts_from_total(None).values()), len(QUESTION_TYPES))

    def test_extracts_command_payloads(self) -> None:
        self.assertEqual(
            _extract_job_text("공고 저장해줘 https://example.com/job."),
            "https://example.com/job",
        )
        self.assertEqual(
            _extract_github_url("GitHub 분석해줘 https://github.com/openai/openai-python."),
            "https://github.com/openai/openai-python",
        )
        self.assertEqual(
            _extract_profile_text("프로필 저장해줘 백엔드 개발자입니다."),
            "백엔드 개발자입니다.",
        )
        self.assertEqual(
            _extract_resume_text("자소서 저장해줘 저는 장애 대응 경험이 있습니다."),
            "저는 장애 대응 경험이 있습니다.",
        )
        self.assertEqual(
            _extract_answer_text("내 답변은 트랜잭션 격리 수준을 확인했습니다."),
            "트랜잭션 격리 수준을 확인했습니다.",
        )

    def test_detects_question_plan_and_candidate_start(self) -> None:
        self.assertTrue(_looks_like_question_plan("1번 공고랑 1번 GitHub로 질문 5개 만들어줘"))
        self.assertTrue(_looks_like_question_plan("면접 질문 후보 생성해줘"))
        self.assertFalse(_looks_like_question_plan("꼬리질문 해줘"))

        self.assertTrue(_looks_like_candidate_interview_start("방금 만든 질문 후보로 면접 시작해줘"))
        self.assertTrue(_looks_like_candidate_interview_start("생성한 질문으로 면접 진행해줘"))
        self.assertFalse(_looks_like_candidate_interview_start("1번 공고로 면접 시작해줘"))

    def test_detects_interview_history_search_and_extracts_query(self) -> None:
        message = "내가 했던 면접중에 웹소켓 관련 질문이 있었나?"

        self.assertTrue(_looks_like_interview_history_search(message))
        self.assertEqual(_extract_interview_history_query(message), "웹소켓")
        self.assertEqual(
            _extract_interview_history_query("메모리 답변 피드백 있는 최근 2주 면접 기록 찾아줘"),
            "메모리 최근 2주",
        )
        self.assertTrue(_looks_like_interview_history_search("이전 면접에서 Redis 질문 물어봤나"))
        self.assertTrue(_looks_like_interview_history_search("메모리 한계 관련 질문 있었나?"))
        self.assertFalse(_looks_like_interview_history_search("최근 리뷰 불러줘"))


class AgentCompoundCommandTest(unittest.TestCase):
    def test_splits_safe_compound_commands(self) -> None:
        cases = {
            "1번 공고 선택하고 공고 목록 보여줘": ["1번 공고 선택", "공고 목록 보여줘"],
            "공고 목록 보여줘 그리고 최근 리뷰 불러줘": ["공고 목록 보여줘", "최근 리뷰 불러줘"],
            "GitHub 분석해줘 https://github.com/a/b 하고 질문 만들어줘": [
                "GitHub 분석해줘 https://github.com/a/b",
                "질문 만들어줘",
            ],
            "프로필 저장해줘 백엔드 개발자입니다 그 다음 자소서 저장해줘 장애 대응 경험이 있습니다": [
                "프로필 저장해줘 백엔드 개발자입니다",
                "자소서 저장해줘 장애 대응 경험이 있습니다",
            ],
        }

        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(_split_compound_message(message), expected)

    def test_does_not_split_interview_answers(self) -> None:
        message = "내 답변은 트랜잭션을 확인하고 락 범위를 줄였습니다"
        self.assertEqual(_split_compound_message(message), [message])


if __name__ == "__main__":
    unittest.main()
