# FastAPI Backend

LoL Riot API 데이터를 조회한 뒤 Databricks Job을 실행하고, 최종 AI 코칭 결과를 반환하는 FastAPI 백엔드입니다.

## 1. 설치

Python 가상환경을 만든 뒤 의존성을 설치합니다.

```bash
pip install -r requirements.txt
```

## 2. 환경 변수

실제 `.env` 파일은 GitHub에 올리지 않습니다.

프로젝트 폴더에 `.env` 파일을 만들고 아래 값을 설정합니다.

```env
RIOT_API_KEY=
DATABRICKS_HOST=
DATABRICKS_TOKEN=
DATABRICKS_JOB_ID=
```

- `RIOT_API_KEY`: 각자 발급받은 Riot Development API Key
- `DATABRICKS_HOST`: 팀 Databricks Workspace 주소
- `DATABRICKS_TOKEN`: 각자 사용하는 Databricks 인증 토큰
- `DATABRICKS_JOB_ID`: Unified Inference Databricks Job ID

Azure OpenAI API Key는 FastAPI `.env`에 넣지 않습니다.
Databricks Job 내부에서 `lol-ai-secrets` Secret Scope의 `azure-openai-api-key`를 읽어 사용합니다.

## 3. 실행

```bash
uvicorn main:app --reload
```

실행 후 Swagger:

```text
http://127.0.0.1:8000/docs
```

## 4. 주요 API

### AI 코칭

```http
GET /coach/riot/{game_name}/{tag_line}
```

예시:

```text
GET /coach/riot/Hide%20on%20bush/KR1
```

처리 흐름:

```text
Riot ID
→ Riot API 최근 솔로랭크 경기 조회
→ FastAPI Feature 생성
→ Databricks Unified Inference Job
→ ML / Benchmark / Playstyle / Combat 분석
→ Azure OpenAI
→ FastAPI JSON 응답
```

## 5. 프론트에서 주로 사용할 응답 필드

```text
status
game_name
tag_line
tier
match_count

result.player.main_champion
result.player.main_position
result.player.tactical_role
result.player.power_curve

result.coaching.play_summary
result.coaching.playstyle_summary
result.coaching.strengths
result.coaching.improvements
result.coaching.primary_goal
result.coaching.recent_growth
result.coaching.coaching
result.coaching.overall_comment
```

성공 시 최상위 `status`는 `success`, Databricks 분석 결과의 `result.status`는 `ok`입니다.

## 6. 보안

아래 파일/값은 GitHub에 올리지 않습니다.

```text
.env
.venv/
__pycache__/
RIOT_API_KEY 실제 값
DATABRICKS_TOKEN 실제 값
Azure OpenAI API Key 실제 값
```

`.gitignore`에 최소한 아래 항목이 포함되어 있어야 합니다.

```gitignore
.env
.venv/
__pycache__/
```
