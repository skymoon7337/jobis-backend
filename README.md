# jobis backend

로컬 개인용 취업/면접 에이전트 백엔드입니다. FastAPI로 실행되며, 프론트엔드는 `jobis-frontend`의 Next.js 앱을 사용합니다.

## 현재 구현

- 채팅 명령 처리: `/api/agent`
- 프로필/자소서 저장
- 공고 저장, 목록, 선택, 삭제
- GitHub 레포 분석, 목록, 삭제
- 공고 + GitHub 기반 질문 후보 생성
- 질문 후보 기반 면접 시작
- 면접 답변 제출, 다음 질문, 꼬리질문, 추가질문
- 최종 리뷰 생성, 최근 리뷰 조회
- 진행 중 면접 이어하기
- background job과 pending command
- 같은 입력의 분석 job 캐시 재사용
- 약점 학습: 최종 리뷰에서 `weakness_items` 누적
- 면접 기억: 질문/답변/리뷰를 `memory_items`로 저장
- keyword 기반 기억 검색
- 이전 면접 기록 검색
  - 자연어로 과거 면접 질문/답변/피드백/최종 리뷰 검색
  - 결과 없음 시 최근 리뷰로 임의 이동하지 않음
  - 같은 세션의 같은 문항은 질문/답변/최종 리뷰를 통합 결과로 병합
  - 대표 4개 결과와 다음 4개 결과(`next_matches`)를 분리해 반환
  - `최근 N주/개월`, `답변/피드백 있는 것만` 필터 지원
  - 회사명/GitHub 프로젝트명은 검색 색인으로 쓰되 표시 excerpt에는 섞지 않음
  - 세부검색 추천 prompt(`refine_suggestions`) 반환
- 테스트 user key 분리와 민감 로그 마스킹

## 아직 안 한 것

- 이전 면접 검색 품질 고도화
  - 동의어/가중치: `메모리 한계`, `OOM`, `JVM 메모리 초과` 같은 표현 묶기
  - embedding 기반 의미 검색
- 검색 필터 UI와 맞물리는 서버 필터 확장
  - 회사, GitHub, 질문 타입, 기간, 답변 있음 여부를 구조화된 파라미터로 받을 수 있게 확장
- LLM JSON 기반 약점 추출 고도화
- 학습 데이터 중복/노이즈 튜닝
- 배포/로그인

위 항목은 실제 사용 데이터가 어느 정도 쌓인 뒤 붙이는 쪽이 좋습니다.

## 환경 변수

`.env.example`을 참고해 `.env`를 만듭니다.

```bash
JOBIS_MODE=local
JOBIS_DEFAULT_USER_KEY=local
JOBIS_PROVIDER=gemini

GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash-lite
JOBIS_MAIN_CHAT_MODEL=gemini-2.5-flash-lite
JOBIS_ANALYSIS_MODEL=gemini-2.5-flash
JOBIS_EMBEDDING_MODEL=gemini-embedding-2
GEMINI_FALLBACK_MODELS=gemini-2.5-flash-lite

DB_HOST=localhost
DB_PORT=5432
DB_NAME=jobis
DB_USERNAME=...
DB_PASSWORD=...
```

`JOBIS_EMBEDDING_MODEL`은 지금은 후보값만 유지합니다. 실제 vector 검색은 아직 구현하지 않았습니다.

## 실행

의존성 설치:

```bash
uv sync
```

DB 준비:

```bash
docker compose up -d
uv run python -m db.init_db
```

기존 Postgres를 쓰는 경우에는 `jobis` DB만 만들어두면 됩니다.

```bash
docker exec -it postgres18 psql -U postgres -c "CREATE DATABASE jobis;"
```

백엔드 실행:

```bash
uv run uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

프론트 실행:

```bash
cd ../jobis-frontend
npm run dev
```

접속:

```text
http://localhost:3000
```

## 주요 채팅 명령

```text
공고 목록 보여줘
공고 저장해줘 https://example.com/job
GitHub 분석해줘 https://github.com/user/repo

1번 공고랑 1번 GitHub로 질문 5개 만들어줘
방금 만든 질문 후보로 면접 시작해줘
현재 질문 보여줘
내 답변은 ...
다음 질문
꼬리질문 해줘
최종 리뷰 만들어줘
최근 리뷰 불러줘
내가 했던 면접중에 웹소켓 관련 질문이 있었나?
메모리 관련 질문 답변 있었나?
메모리 답변 피드백 있는 최근 2주 면접 기록 찾아줘

내 약점 보여줘
이번에 뭐 연습하면 돼?
트랜잭션 피드백 찾아줘
약점 학습 초기화 확인
```

## 동작 메모

- `새로`, `다시`, `재분석`, `재생성`, `갱신`, `업데이트`가 들어간 명령은 기존 캐시를 건너뛰고 새 job을 만듭니다.
- 진행 중 면접에서 `면접 시작해줘`를 다시 보내면 새 면접을 만들지 않고 현재 면접을 보여줍니다.
- `delete_user_data()`는 기본적으로 `test-agent-*` 사용자만 삭제합니다.
- action/pending 공개 payload는 API key, token, password류를 마스킹합니다.
- 공고 원문과 GitHub 분석 전체는 장기기억 본문으로 복제하지 않고, 면접 세션의 source metadata로 연결합니다.
- 기억 검색은 현재 keyword 기반입니다.
- 이전 면접 기록 검색도 현재 keyword 기반입니다. 결과 수가 많아질 때는 전체를 반환하지 않고 대표 결과와 다음 묶음만 반환합니다.
- 세부검색 추천은 자동 실행용이 아니라 프론트 입력창에 채우는 prompt입니다.

## 테스트

단위 테스트:

```bash
uv run python -m unittest discover -s tests -v
```

컴파일 확인:

```bash
uv run python -m compileall api services db tests scripts
```

실행 중인 API 대상 smoke:

```bash
uv run python scripts/agent_smoke.py
```

Gemini 모델 확인:

```bash
uv run python scripts/gemini_model_smoke.py
```

LLM 포함 전체 시나리오 smoke:

```bash
uv run python scripts/agent_full_scenario_smoke.py --run-llm
```

전체 시나리오 smoke는 질문 생성, 면접 시작, 답변, 최종 리뷰, 약점 학습, memory 검색까지 확인합니다.
