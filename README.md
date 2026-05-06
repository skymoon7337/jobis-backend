## jobis backend

텔레그램으로 먼저 검증하는 개인 맞춤 면접 에이전트 MVP입니다.

사용자의 프로필, 자소서, GitHub 레포, 채용공고를 바탕으로 면접 질문을 만들고, 답변을 저장한 뒤 전체 피드백과 약점 요약을 제공합니다. 현재는 배포 없이 로컬 맥북에서 Telegram bot으로 실행합니다.

## 현재 구현된 것

### 입력/분석

- `/profile` 관심 직무, 경력, 학력, 기술스택 저장
- `/resume` 자소서 텍스트 저장
- `/github` GitHub 레포 URL 입력 후 README, 파일 구조, 주요 코드 기반 분석
- `/job` 채용공고 URL 또는 본문 저장
- `/analyze` 프로필, 자소서, GitHub, 공고를 묶어 통합 분석
- 자료가 바뀌면 기존 통합 분석을 비우고 `/analyze` 재실행 안내

### 면접

- `/interview` 5문항 면접 시작
- 기본 구성:
  - CS 기본기
  - 언어
  - 기술스택
  - 프로젝트/GitHub
  - 프로젝트/자소서
- `/followup` 방금 답변에 대한 꼬리질문 생성
- `/another` 같은 분야의 추가질문 생성
- `/next` 다음 기본 질문 또는 전체 평가로 이동
- 질문 번호:
  - `1-1` 기본 질문
  - `1-2` 같은 섹션 추가질문
  - `1-2-f1` 특정 답변의 꼬리질문

### 저장/복구

- Postgres에 사용자 입력, 분석 결과, 면접 세션, 생성 질문, 답변 기록 저장
- 서버가 꺼졌다 켜져도 `/continue`로 진행 중 면접 복원
- 서버 재시작 시 진행 중 면접이 있으면 자동 안내
- 서버가 꺼져 있는 동안 들어온 Telegram 메시지는 처리하지 않음
- 서버 재시작 시 진행 중 면접이 없고 저장된 입력 상태가 있으면 현재 상태와 다음 추천 명령어 안내

### 보기/복기

- `/show_status` 현재 입력 상태 보기
- `/show_github` 저장된 GitHub 상세 분석 보기
- `/show_analyze` 저장된 통합 상세 분석 보기
- `/show_history` 최근 면접 기록 보기
- `/show_feedback` 최근 완료 면접의 전체 피드백 다시 보기
- `/show_weakness` 최근 면접 피드백에서 저장된 약점 요약 보기

## 실행 준비

`.env.example`을 참고해서 `.env`를 만듭니다.

```bash
TELEGRAM_BOT_TOKEN=텔레그램_BotFather에서_받은_토큰
ALLOWED_TELEGRAM_CHAT_IDS=허용할_Telegram_chat_id
JOBIS_PROVIDER=gemini
GEMINI_API_KEY=Google_AI_Studio에서_받은_키
GEMINI_MODEL=gemini-3.1-flash-lite-preview
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.2

DB_HOST=localhost
DB_PORT=5432
DB_NAME=jobis
DB_USERNAME=로컬_DB_사용자명
DB_PASSWORD=로컬_DB_비밀번호

SEND_RESUME_NOTICE_ON_START=true
SEND_PROGRESS_NOTICE_ON_START=true
DROP_PENDING_UPDATES_ON_START=true
```

`ALLOWED_TELEGRAM_CHAT_IDS`는 쉼표로 여러 개를 넣을 수 있습니다. 예: `123456789,987654321`
이 값이 비어 있으면 봇이 실행되지 않습니다.

의존성을 설치합니다.

```bash
uv sync
```

## Postgres 실행

이미 `localhost:5432`에 Postgres가 떠 있다면 새 컨테이너를 만들 필요는 없습니다. 기존 컨테이너를 쓰는 경우 `jobis` DB만 만들어주면 됩니다.

```bash
docker exec -it postgres18 psql -U postgres -c "CREATE DATABASE jobis;"
```

새 컨테이너가 필요할 때만 Docker Compose로 로컬 Postgres를 실행합니다.

```bash
docker compose up -d
```

DB 테이블과 개발용 컬럼 마이그레이션을 적용합니다.

```bash
uv run python -m db.init_db
```

## 텔레그램 봇 실행

```bash
uv run python bot.py
```

텔레그램에서 BotFather로 만든 봇에게 `/start`를 보내면 됩니다.

## 권장 사용 순서

```bash
/profile 백엔드 주니어 개발자, 신입, 컴퓨터공학 전공, Java/Spring Boot, MySQL, Docker
/resume 자소서 텍스트
/github GitHub 레포 URL
/job 공고 URL 또는 공고 본문
/analyze
/interview
```

면접 중에는 답변을 보낸 뒤 아래 중 하나를 선택합니다.

```bash
/next
/followup
/another
```

서버를 껐다 켠 뒤 진행 중 면접을 이어가려면:

```bash
/continue
```

## 명령어

### 시작/입력

- `/start` 진행 순서와 현재 상태 보기
- `/help` 전체 명령어 보기
- `/profile` 관심 직무, 경력, 학력, 기술스택 입력
- `/resume` 자소서 텍스트 입력
- `/github` GitHub 레포 분석 실행
- `/job` 공고 URL 또는 본문 입력
- `/analyze` 입력 자료 통합 분석 실행

### 면접

- `/interview` 5문항 면접 시작
- `/continue` 진행 중이던 면접 이어하기
- `/next` 다음 질문 또는 전체 평가로 이동
- `/followup` 방금 답변 꼬리질문
- `/another` 같은 분야 추가질문
- `/end` 현재 면접 종료

### 보기

- `/show_status` 현재 입력 상태 보기
- `/show_history` 최근 면접 기록 보기
- `/show_feedback` 최근 전체 면접 피드백 보기
- `/show_weakness` 최근 약점 요약 보기
- `/show_github` 저장된 GitHub 상세 분석 보기
- `/show_analyze` 저장된 통합 상세 분석 보기

### 기타

- `/review` 자소서 피드백
- `/reset` 프로필, 자소서, GitHub, 공고, 통합 분석 초기화

## 데이터 저장 구조

### 저장되는 것

- 사용자 프로필
- 자소서
- GitHub URL과 분석 결과
- 채용공고 본문
- 통합 분석 결과
- 면접 세션 상태
- 생성된 기본 질문과 보너스 질문
- 답변 완료된 질문/답변
- 전체 면접 피드백
- 약점 요약

### 아직 저장하지 않는 것

- `/resume` 입력 대기 같은 일시적인 입력 대기 상태
- `/github`, `/analyze` 같은 LLM 작업이 실행 중이던 상태
- 개별 답변마다 즉시 생성되는 피드백
- 전체 피드백의 버전 히스토리

## 서버 재시작 정책

- 서버가 꺼져 있는 동안 들어온 Telegram 메시지는 무시합니다.
- `.env`의 `ALLOWED_TELEGRAM_CHAT_IDS`에 있는 사용자만 봇을 사용할 수 있습니다.
- 진행 중인 면접이 있으면 서버 시작 시 `/continue`, `/end` 안내를 보냅니다.
- 진행 중인 면접은 없지만 저장된 입력 상태가 있으면 현재 상태와 다음 추천 명령어를 보냅니다.
- 아무 데이터도 없는 첫 상태에서는 조용히 대기합니다.

## 현재 범위

- 로컬 Telegram bot MVP
- 로그인 없음
- 웹 프론트엔드 없음
- 배포 없음
- 음성/캠 없음
- LLM은 Gemini API 기본 사용
- OpenAI provider 코드도 있으나 기본 provider는 Gemini

## 앞으로 할 일

### 우선순위 높음

- `/show_questions` 추가: 저장된 질문/답변 원문 복기
- reset 명령 분리:
  - `/reset_context` 입력 자료 초기화
  - `/reset_all` 면접 기록까지 전체 초기화
- `/show_status`에 최근 면접 상태와 최근 피드백 여부 추가
- 새 `/interview` 시작 시 기존 active 면접 종료 안내 문구 추가

### 품질/최적화

- `/review` 결과 캐싱
- GitHub 분석 토큰 최적화
- Gemini 모델 fallback
- 질문 구성 커스터마이징
- 지난 약점 기반 다음 면접 질문 생성
- 질문 유형별 점수 추이 저장

### 입력 확장

- PDF 자소서 업로드/파싱
- 여러 GitHub 레포 분석
- 공고 URL 파싱 개선
- 텔레그램 음성 메시지 STT

### 장기 계획

- 웹 프론트엔드 추가
- 음성 면접
- 캠 기반 신호 분석
- 사용자별 대시보드

나중에 웹이나 FastAPI API를 붙일 때도 `services/` 안의 GitHub 분석, LLM 호출, 세션 설계 로직은 최대한 재사용합니다.
