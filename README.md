# LoL Playstyle Analysis

Riot API로 입력한 Riot ID의 **한국 서버(KR) 최근 솔로랭크** 데이터를 수집해 분석용 XLSX를 만드는 팀 프로젝트입니다. Match‑V5에서 `queue=420`인 `RANKED_SOLO_5x5` 경기만 최대 20개 가져오며, `KR_`로 시작하는 Match ID만 저장합니다. 한 경기의 참가자 10명을 각각 한 행으로 기록하므로 정상 수집 시 최대 200행입니다.

> `.env`의 Riot API Key, 생성된 XLSX, 원본 JSON과 로그는 절대로 GitHub에 올리지 마세요. 이 저장소의 `.gitignore`가 해당 파일을 제외하지만 commit 전 `git status`를 다시 확인해야 합니다.

## 폴더 구조

```text
src/riot_api/                     API 클라이언트, 수집기, 파서, 스키마, XLSX 내보내기
scripts/export_recent_matches.py  실행 진입점
tests/fixtures/                   외부 호출 없는 테스트용 응답
tests/                            단위·통합 테스트
docs/                             API 흐름, 컬럼 정의, 협업 안내
data/                             결과 저장 위치(결과 파일은 Git 제외)
.github/                          CI와 Issue/PR 템플릿
```

## 처음 설치하기

Python 3.11 이상과 Git이 필요합니다. 저장소 주소는 GitHub 저장소 페이지의 **Code** 버튼에서 복사합니다.

```powershell
git clone <저장소-주소>
Set-Location lol-playstyle-analysis
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

[Riot Developer Portal](https://developer.riotgames.com/)에서 개발용 API Key를 발급받아 `.env`의 등호 뒤에 입력합니다.

```dotenv
RIOT_API_KEY=RGAPI-발급받은키
```

개발용 키는 24시간마다 만료될 수 있습니다. 만료되면 Developer Portal에서 새 키를 발급받아 로컬 `.env` 값만 교체합니다. `.env.example`에는 실제 키를 넣지 않습니다.

## 데이터 수집

기본 20경기:

```powershell
python scripts/export_recent_matches.py --game-name "Hide on bush" --tag-line "KR1" --count 20
```

출력 위치 지정:

```powershell
python scripts/export_recent_matches.py --game-name "Hide on bush" --tag-line "KR1" --count 20 --output "data/league_data.xlsx"
```

기본 결과는 `data/` 아래에 생성됩니다. 지정한 파일이 이미 있으면 `_1`, `_2`를 붙여 원본을 보존합니다. `--save-raw`는 디버깅용 원본 JSON을 저장하지만 해당 폴더도 Git에서 제외됩니다.

XLSX에는 `league_data`, `검색정보`, `컬럼정의서`, `수집오류` 시트가 있습니다. Timeline 실패 시 경기 상세 행은 남고 `final_*`만 비어 있으며, 랭크·숙련도 등 부분 실패는 `수집오류`에 기록됩니다.

## 테스트와 코드 검사

테스트는 fixture와 mock만 사용하며 실제 Riot API나 API Key를 사용하지 않습니다.

```powershell
python -m pytest
python -m ruff check .
```

## 자주 발생하는 오류

- `401`: API Key 형식이 잘못됐습니다. `.env` 값을 다시 복사합니다.
- `403`: 개발용 Key가 만료됐거나 권한이 없습니다. 새 Key로 `.env`만 교체합니다.
- `404`: Riot ID, KR 소환사 계정 또는 경기 데이터가 없습니다. 게임 이름과 태그를 확인합니다.
- `429`: 호출 한도 초과입니다. 수집기가 `Retry-After`만큼 기다린 뒤 최대 3회 재시도합니다.
- `5xx`: Riot 서비스의 일시 오류입니다. 지수 백오프로 최대 3회 재시도합니다.

브랜치와 Pull Request를 이용한 협업 절차는 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요. API 호출 순서는 [docs/API_FLOW.md](docs/API_FLOW.md), 고정 94개 컬럼은 [docs/COLUMN_DEFINITION.md](docs/COLUMN_DEFINITION.md)에 정리되어 있습니다.

## LoL RAG 및 개인 경기 Data Agent

현지 담당 영역은 Azure AI Search 기반 공식정보 RAG와 개인 경기 Data Agent입니다. Riot ID와 태그라인으로 Queue 420 최근 경기를 조회하고, Python에서 개인 경기 통계를 결정적으로 계산한 뒤 질문을 `official_information`, `personal_match`, `mixed` 경로로 분류합니다. 공식정보 질문은 Azure AI Search의 742개 공식 문서를 검색하며, 개인·혼합 질문은 필요한 경우에만 공식 문서를 결합합니다. 최종 한국어 답변은 Azure OpenAI를 사용해 검색 및 계산 근거 안에서 생성합니다.

주요 기능은 다음과 같습니다.

- 승패, K/D/A, CS·골드·피해·시야 등 개인 경기 통계 계산
- 공식정보·개인 경기·혼합 질문 라우팅과 동일 사용자 경기 Context 재사용
- 아이템 숫자 ID 및 한글 아이템 이름 검색
- Match 최종 아이템과 Timeline 구매 이력·첫 구매 시간 분석
- 완성 장비·조합 재료·소모품·장신구·신발 구매 빈도 분리
- 공식 출처 Citation과 근거 부족 시 `NO_DATA`/`NEEDS_CLARIFICATION` 처리
- Notebook과 웹 API에서 함께 쓰는 `answer_question()` 재사용 인터페이스
- Databricks SOURCE Notebook 예제를 `notebooks/databricks/`에 보관

검증 결과:

- 공식정보 생성 QA: 14/14 PASS
- 개인 경기 분석 QA: 6/6 PASS
- 자유 질문 종합 라우팅: 30/30
- 표적 회귀 테스트: 6/6 PASS
- 개인 아이템 분석: 10/10 PASS
- 아이템 카테고리 검증: 8/8 PASS
- 최종 관련 단위 테스트: 110 passed

실제 Secret과 API Key는 저장소에 포함하지 않습니다. 로컬 실행자는 환경변수에 Azure Search/OpenAI 설정을 제공해야 하며, Databricks 실행자는 자신의 Secret Scope와 환경별 Workspace 설정을 구성해야 합니다. 저장소의 Notebook은 환경 식별자와 Riot ID가 placeholder로 치환된 SOURCE 사본이며, 원본 실행 결과는 포함하지 않습니다.

구조와 설정, `answer_question()` 및 FastAPI 연결 예시는 [docs/RAG_DATA_AGENT.md](docs/RAG_DATA_AGENT.md)를 참고하세요.

## Riot Games 고지

이 프로젝트는 Riot Games가 보증하거나 후원하지 않습니다. Riot Games 및 관련 자산은 각 소유자의 상표입니다. API 사용 시 Riot Developer Portal의 정책과 약관을 준수하세요.
