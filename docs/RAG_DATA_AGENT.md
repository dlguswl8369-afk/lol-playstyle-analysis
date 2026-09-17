# LoL RAG 및 개인 경기 Data Agent

## 전체 구조

```text
자유 질문
  -> 질문 라우팅(official_information / personal_match / mixed)
  -> Riot Queue 420 경기 Context 수집 및 재사용
  -> Python 결정적 통계 계산
  -> 필요한 경우 Azure AI Search 공식 문서 검색
  -> Azure OpenAI 근거 기반 한국어 답변
  -> 통계와 공식 Citation 반환
```

개인 경기 원본은 Azure AI Search에 적재하지 않는다. Search에는 Riot 공식 정적 데이터와 패치 문서 742개만 있으며, 챔피언·아이템·룬·소환사 주문·패치·맵·Queue 질문을 지원한다.

## 주요 모듈

- `routing.py`: 공식정보, 개인 경기, 혼합 질문 분류와 안전한 분류 사유
- `riot_client.py`: Account, Summoner, League, Match, Timeline 호출과 안전한 오류 코드
- `personal_analysis.py`: K/D/A, 승률, 분당 지표, 시야, 상대 포지션, 아이템 이력 통계
- `official_search.py`: Azure AI Search 필터, 아이템 ID·한글 이름 해석, 공식 문서 캐시
- `answer_generation.py`: 검색·통계 근거로 제한된 Azure OpenAI 답변 생성
- `orchestrator.py`: `collect_player_context()`와 `answer_question()`의 재사용 진입점
- `rag/ddragon_items.py`: 공식 Data Dragon 아이템 문서 생성과 export 포함 판정

아이템 분석은 최종 인벤토리와 Timeline의 `ITEM_PURCHASED`, `ITEM_SOLD`, `ITEM_DESTROYED`, `ITEM_UNDO`를 분리한다. 구매 통계는 전체, 완성 장비, 조합 재료, 소모품, 장신구, 신발, 미분류 항목을 함께 제공한다.

## `answer_question()` 사용 예시

```python
import os

from azure.identity import ClientSecretCredential
from lol_rag import AgentDependencies, answer_question, collect_player_context

credential = ClientSecretCredential(
    tenant_id=os.environ["AZURE_TENANT_ID"],
    client_id=os.environ["AZURE_CLIENT_ID"],
    client_secret=os.environ["AZURE_CLIENT_SECRET"],
)
dependencies = AgentDependencies(
    entra_credential=credential,
    riot_api_key=os.environ["RIOT_API_KEY"],
)

context = collect_player_context(
    riot_id=os.environ["RIOT_ID"],
    tag_line=os.environ["RIOT_TAG_LINE"],
    match_count=15,
    dependencies=dependencies,
)
result = answer_question(
    question="최근 경기에서 내 최종 아이템 빌드를 알려줘.",
    riot_id=os.environ["RIOT_ID"],
    tag_line=os.environ["RIOT_TAG_LINE"],
    match_count=15,
    dependencies=dependencies,
    player_context=context,
)
```

같은 사용자의 여러 질문은 하나의 `PlayerContext`와 `AgentDependencies.item_cache`를 재사용해 Riot 및 Search 중복 호출을 줄인다.

## FastAPI 연결 예시

```python
from fastapi import FastAPI
from pydantic import BaseModel, Field

from lol_rag import answer_question

app = FastAPI()


class QuestionRequest(BaseModel):
    question: str
    riot_id: str | None = None
    tag_line: str | None = None
    match_count: int = Field(default=15, ge=1, le=20)


@app.post("/qa")
def qa(request: QuestionRequest):
    return answer_question(
        question=request.question,
        riot_id=request.riot_id,
        tag_line=request.tag_line,
        match_count=request.match_count,
        dependencies=app.state.agent_dependencies,
    )
```

운영 서비스에서는 요청 검증, 사용자별 Context 캐시 수명, 타임아웃, 호출량 제한과 개인정보 마스킹을 추가해야 한다.

## 지원 Route

- `official_information`: 공식 챔피언·아이템·룬·주문·패치·맵·Queue 설명
- `personal_match`: 개인 승률·KDA·플레이스타일·시야·상대 비교·아이템 빌드와 구매 이력
- `mixed`: 개인 통계와 공식 효과 또는 특징을 동시에 요구하는 질문

데이터가 부족하면 수치를 만들지 않고 `NO_DATA` 또는 `NEEDS_CLARIFICATION`을 반환한다. 개인 질문에서 공식정보가 필요하지 않으면 Search를 호출하지 않으며, 결정적 통계 답변은 OpenAI 없이 생성할 수 있다.

## 로컬 설정

실제 값은 `.env`나 운영 Secret 저장소에만 둔다. 저장소에는 커밋하지 않는다.

```text
AZURE_TENANT_ID
AZURE_CLIENT_ID
AZURE_CLIENT_SECRET
AZURE_SEARCH_ENDPOINT
AZURE_SEARCH_INDEX
AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_DEPLOYMENT
RIOT_API_KEY
RIOT_ID
RIOT_TAG_LINE
```

Riot Development API Key는 짧은 주기로 만료될 수 있다. 만료된 Key를 코드나 Notebook에 붙여 넣지 말고 Riot Developer Portal에서 재발급한 뒤 로컬 환경 또는 Secret 저장소만 갱신한다.

## Databricks 설정

Databricks에서는 환경별 Workspace ID, 사용자, Git 경로와 Azure endpoint를 Notebook의 placeholder 또는 환경설정으로 제공한다. Tenant ID, Client ID, Client Secret과 Riot API Key는 Databricks-backed Secret Scope 또는 동등한 보안 저장소에서 읽고 값은 출력하지 않는다.

SOURCE Notebook 사본은 `notebooks/databricks/`에 있으며 실행 결과, 실제 Riot ID·태그라인, PUUID, Match ID, 사용자 이메일, Workspace·Compute·Object ID 및 Azure 식별자를 포함하지 않는다.

## 보안 원칙

- Azure Search와 Azure OpenAI는 Microsoft Entra ID 인증을 사용한다.
- Riot API Key는 `X-Riot-Token` 헤더에만 전달하고 URL, 로그, 예외에 포함하지 않는다.
- Secret, Token, Authorization 값, PUUID와 원본 Match ID를 응답이나 로그에 출력하지 않는다.
- 개인 경기 원본을 Search에 적재하지 않는다.
- Citation은 실제 검색 Context 문서만 참조한다.
- API 오류는 안전한 단계·상태 코드로만 기록한다.

## 검증 현황

- 공식정보 생성 QA: 14/14 PASS
- 개인 경기 분석 QA: 6/6 PASS
- 자유 질문 라우팅: 30/30
- 표적 회귀 테스트: 6/6 PASS
- 개인 아이템 분석: 10/10 PASS
- 아이템 카테고리 검증: 8/8 PASS
- 최종 관련 단위 테스트: 110 passed
