## jobis backend

로컬 웹에서 사용하는 개인 맞춤 면접 에이전트 MVP입니다.

사용자의 프로필, 자소서, GitHub 레포, 채용공고를 바탕으로 면접 질문 후보를 만들고, 선택한 질문으로 면접을 진행한 뒤 세션 리뷰와 약점 요약을 제공합니다. 기본 실행은 로컬 FastAPI + 웹 프론트엔드입니다.

## 현재 구현된 것

### 입력 관리

- 프로필 저장
- 자소서 저장
- GitHub 레포 URL 입력 후 README, 파일 구조, 주요 코드 기반 분석
- 채용공고 추가, 목록 확인, 선택/삭제

### 면접

- 공고와 GitHub 프로젝트를 선택한 뒤 질문 후보 생성
- 질문 유형별 개수 조절:
  - CS 기본기
  - 언어
  - 기술스택
  - 프로젝트/GitHub
  - 프로젝트/자소서
- 질문 후보 중 사용할 질문을 선택해 면접 시작
- 방금 답변에 대한 꼬리질문 생성
- 같은 분야의 추가질문 생성
- 다음 기본 질문 또는 전체 평가로 이동
- 질문 번호:
  - `1-1` 기본 질문
  - `1-2` 같은 섹션 추가질문
  - `1-2-f1` 특정 답변의 꼬리질문

### 저장/복구

- Postgres에 사용자 입력, GitHub 분석 결과, 공고, 면접 세션, 생성 질문, 답변 기록 저장
- 서버가 꺼졌다 켜져도 진행 중 면접 복원
- 면접 세션 시작 시 선택한 공고와 GitHub 프로젝트 스냅샷 저장

### 보기/복기

- 로컬 웹에서 현재 입력 상태, GitHub 분석, 면접 진행, 세션 리뷰 조회
- 리뷰 탭에서 세션별 공고/GitHub 스냅샷, 질문/답변 기록, 종합 리뷰 확인
- 약점 분석 탭은 누적 개인화 대시보드로 개편 예정

## 실행 준비

`.env.example`을 참고해서 `.env`를 만듭니다.

```bash
JOBIS_MODE=local
JOBIS_DEFAULT_USER_KEY=local
JOBIS_PROVIDER=gemini
GEMINI_API_KEY=Google_AI_Studio에서_받은_키
GEMINI_MODEL=gemini-3.1-flash-lite-preview
GEMINI_FALLBACK_MODELS=gemini-2.5-flash-lite
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.2
JOBIS_LLM_RETRY_DELAYS=5,10,20

DB_HOST=localhost
DB_PORT=5432
DB_NAME=jobis
DB_USERNAME=로컬_DB_사용자명
DB_PASSWORD=로컬_DB_비밀번호
```

Gemini가 `503 UNAVAILABLE` 또는 high demand를 반환하면 `JOBIS_LLM_RETRY_DELAYS` 간격으로 재시도합니다. `GEMINI_FALLBACK_MODELS`에 쉼표로 대체 모델을 넣으면 기본 모델이 일시 과부하일 때 같은 요청을 대체 모델에도 시도합니다.

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

## 로컬 API 실행

```bash
uv run uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

프론트엔드는 `jobis-frontend`에서 실행합니다.

```bash
npm run dev
```

브라우저에서 `http://localhost:3000`을 엽니다.

## 권장 사용 순서

1. 입력 관리에서 프로필과 자소서를 저장합니다.
2. GitHub 저장소를 분석해 보관합니다.
3. 공고를 저장합니다.
4. 면접 탭에서 공고와 GitHub 프로젝트를 선택합니다.
5. 질문 구성을 정하고 질문 후보를 만듭니다.
6. 사용할 질문을 선택해 면접을 시작합니다.

면접 중에는 답변을 보낸 뒤 아래 중 하나를 선택합니다.

다음 질문, 꼬리질문, 추가질문 중 하나를 웹 화면에서 선택할 수 있습니다.

서버를 껐다 켠 뒤 다시 접속하면 진행 중인 면접 세션을 조회해 이어갈 수 있습니다.

## 주요 기능

### 시작/입력

- 프로필 저장
- 자소서 저장
- GitHub 레포 분석
- 공고 추가/목록/선택/삭제

### 면접

- 질문 후보 만들기
- 선택한 질문으로 면접 시작
- 다음 질문 또는 전체 평가로 이동
- 방금 답변 꼬리질문
- 같은 분야 추가질문
- 현재 면접 종료

### 공고 관리

- `/job` 저장된 공고 목록과 사용법 보기
- `/job add` 다음 메시지로 공고 URL 또는 본문 추가
- `/job add 공고본문` 공고 바로 추가
- `/job show` 현재 선택된 공고 요약 보기
- `/job select 2` 2번 공고를 현재 면접 기준으로 선택
- `/job delete 2` 2번 공고 삭제

### 보기

- `/show_status` 현재 입력 상태 보기
- `/show_history` 최근 면접 기록 보기
- `/show_feedback` 최근 전체 면접 피드백 보기
- `/show_weakness` 최근 약점 요약 보기
- `/show_github` 저장된 GitHub 상세 분석 보기
- `/show_analyze` 저장된 통합 상세 분석 보기

### 기타

- 리뷰 탭에서 세션별 질문/답변과 종합 리뷰 확인
- 입력 초기화는 프로필, 자소서, GitHub 분석, 공고만 삭제하고 면접 기록/피드백/약점 요약은 유지

## 민감 데이터 주의

jobis는 로컬 MVP지만, 입력한 자소서와 면접 답변이 다음 위치를 거칩니다.

- 로컬 웹 브라우저와 FastAPI 서버
- Gemini 또는 OpenAI API
- 로컬 Postgres DB

주민등록번호, 전화번호, 주소, 비밀번호, API 키, 회사 내부 정보처럼 민감한 정보는 입력하지 않는 것을 권장합니다.

## 데이터 저장 구조

### 저장되는 것

- 사용자 프로필
- 자소서
- GitHub URL과 분석 결과
- 채용공고 목록, 원본 링크, 요약, 현재 선택된 공고
- 면접 세션 상태
- 면접 세션에서 선택한 공고/GitHub 스냅샷
- 생성된 기본 질문과 보너스 질문
- 답변 완료된 질문/답변
- 전체 면접 피드백
- 약점 요약

### 아직 저장하지 않는 것

- 자소서 입력 대기 같은 일시적인 입력 대기 상태
- GitHub 분석 같은 LLM 작업이 실행 중이던 상태
- 개별 답변마다 즉시 생성되는 피드백
- 전체 피드백의 버전 히스토리

## 서버 재시작 정책

- 진행 중인 면접은 DB의 active 세션으로 복원됩니다.
- 서버가 꺼져 있는 동안의 브라우저 요청은 처리되지 않습니다.
- 다시 접속하면 프론트엔드가 현재 세션과 저장 자료를 조회합니다.

## 현재 범위

- 로컬 FastAPI + 웹 프론트엔드 MVP
- 웹 앱 중심 MVP
- 로그인 없음. 현재는 `JOBIS_DEFAULT_USER_KEY` 또는 `X-Jobis-User-Key` 요청 헤더로 사용자를 구분합니다.
- 배포 없음
- 음성/캠 없음
- LLM은 Gemini API 기본 사용
- OpenAI provider 코드도 있으나 기본 provider는 Gemini

## 앞으로 할 일

### 우선순위 높음

- 리뷰 탭에서 저장된 질문/답변 원문 복기 개선
- reset 기능 분리:
  - 입력 자료 초기화
  - 면접 기록까지 전체 초기화
- 상태 화면에 최근 면접 상태와 최근 피드백 여부 추가
- 새 면접 시작 시 기존 active 면접 종료 안내 문구 추가

### 품질/최적화

- 리뷰 결과 캐싱
- GitHub 분석 토큰 최적화
- 배포 또는 LAN 공유 전에 공고 URL 자동 읽기 SSRF 방어 보강
  - 현재 로컬 MVP에서는 localhost/내부 IP 직접 입력을 막고 있음
  - 외부 공개 전에 허용 도메인 방식 또는 연결 단계 IP 검증으로 DNS rebinding까지 방어 필요
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
