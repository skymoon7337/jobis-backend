## jobis backend

로컬에서 텔레그램으로 먼저 검증하는 개인 면접 에이전트 MVP입니다.

### 실행 준비

`.env.example`을 참고해서 `.env`를 만듭니다.

```bash
TELEGRAM_BOT_TOKEN=텔레그램_BotFather에서_받은_토큰
JOBIS_PROVIDER=gemini
GEMINI_API_KEY=Google_AI_Studio에서_받은_키
GEMINI_MODEL=gemini-2.5-flash-lite
OPENAI_API_KEY=OpenAI_API_키
OPENAI_MODEL=gpt-5.2

DB_HOST=localhost
DB_PORT=5432
DB_NAME=jobis
DB_USERNAME=postgres
DB_PASSWORD=postgres
```

의존성을 설치합니다.

```bash
uv sync
```

### Postgres 실행

이미 `localhost:5432`에 Postgres가 떠 있다면 이 단계는 건너뜁니다.
새 컨테이너가 필요할 때만 Docker Compose로 로컬 Postgres를 실행합니다.

```bash
docker compose up -d
```

기존 컨테이너를 쓰는 경우 `jobis` DB만 만들어주면 됩니다.

```bash
docker exec -it postgres18 psql -U postgres -c "CREATE DATABASE jobis;"
```

DB 테이블을 생성합니다.

```bash
uv run python -m db.init_db
```

### 텔레그램 봇 실행

배포 없이 내 맥북에서만 실행합니다.

```bash
uv run python bot.py
```

텔레그램에서 BotFather로 만든 봇에게 `/start`를 보내면 됩니다.

### 명령어

- `/start` 사용법 보기
- `/profile` 관심 직무, 경력, 학력, 기술스택 입력
- `/resume` 자소서 텍스트 입력
- `/github` GitHub 레포 URL 입력
- `/job` 공고 URL 또는 공고 본문 입력
- `/interview` 5턴 채팅 면접 시작
- `/review` 자소서 피드백
- `/status` 현재 입력 상태 확인
- `/end` 면접 종료
- `/reset` 전체 초기화

### 현재 범위

- 로그인 없음
- 배포 없음
- DB 저장 없음
- 텔레그램 채팅방별 메모리에만 세션 저장
- 음성/캠 없음

나중에 웹이나 FastAPI API를 붙일 때도 `services/` 안의 로직은 그대로 재사용합니다.
