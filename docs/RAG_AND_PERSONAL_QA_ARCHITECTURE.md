# RAG 및 개인 경기 질의응답 로컬 아키텍처

## 범위

이번 단계는 로컬 데이터 준비, 구조화된 계산, 질문 라우팅, 도구 계약 검증만 수행한다. Azure, Databricks, Azure AI Search, Azure OpenAI, Microsoft Foundry와 Riot API에는 연결하지 않는다.

공식 정보 질문은 741개 Riot 공식 문서를 검색하는 `knowledge_query`, 개인 수치 질문은 구조화된 경기 표를 계산하는 `personal_match_query`, 두 근거가 모두 필요한 질문은 `hybrid_query`로 분류한다. 자연어 답변보다 구조화된 근거 JSON을 먼저 만든다.

## 로컬 흐름

```text
공식 패키지 ZIP ── prepare_rag_search_data.py ── rag_documents.jsonl
                                                     │
질문 ── query_router.py ── knowledge_query ──────────┤ 향후 Azure AI Search
                        ├─ personal_match_query ──────┤ 향후 Databricks SQL
                        └─ hybrid_query ──────────────┘ 향후 Foundry Agent

팀 데이터 ZIP ── validate_personal_qa_data.py ── matches.jsonl
                                             ├─ match_participants.jsonl
                                             └─ player_match_features.jsonl
```

기존 Data Dragon, 패치노트, 게임 상수 추출기는 원천 수집 책임을 유지한다. 이번 구현은 이미 만들어진 RAG 문서를 공통 검색 스키마로 배열하는 통합 단계이며 원천 추출을 중복하지 않는다. 기존 Riot API 수집기도 변경하지 않는다.

## 질문 처리 정책 초안

1. 공식 게임 지식은 등록된 Riot 공식 문서만 사용한다.
2. 개인 경기 수치는 구조화된 조회 결과만 사용하며 계산되지 않은 값은 추측하지 않는다.
3. 상대 플레이어는 익명 ID와 챔피언만 표시하고 실제 신원은 표시하지 않는다.
4. 검색 결과나 경기 데이터가 부족하면 `unavailable` 사유를 함께 표시한다.
5. 패치 답변에는 원문의 패치 버전을 표시한다. Data Dragon `16.18.1`과 패치노트 `26.18`을 같은 버전 문자열로 바꾸지 않는다.
6. Queue와 Map을 확인해 다른 게임 모드와 소환사의 협곡을 구분한다.
7. 가능한 경우 공식 출처 URL을 근거에 포함한다.
8. 통계 차이를 패배의 확정적 원인으로 단정하지 않는다.
9. 모욕적이거나 과도하게 부정적인 표현을 사용하지 않는다.
10. 결론과 함께 근거 수치, 표본 수, 누락 데이터 한계를 표시한다.

## 재현성과 보안

- 원본 ZIP은 읽기 전용이며 출력에는 SHA-256만 기록한다.
- 실제 Match ID와 플레이어 식별자는 `MATCH_NNN`, `TARGET_PLAYER`, `PLAYER_NNNNN`으로 대체한다.
- 관계표는 `data/local/`에만 두고 Git에서 제외한다.
- 원본 CSV, XLSX, Timeline은 저장소에 복사하지 않는다.
- 처리 결과는 `data/processed/`에 두고 Git에서 제외한다.
- 0초 경기는 분당 지표를 `null`로 만들며 5분 미만 경기는 `is_remake` 후보로 표시한다.

## Azure 연결 후 작업

Databricks에는 `matches`, `match_participants`, `timeline_features`, `player_match_features` Delta Table을 만든다. Azure AI Search에는 `document_id`를 키로 하는 RAG 인덱스를 만들고 `document_type`, `locale`, `version`, `patch_version`, `category`, `source_id`를 필터 가능 필드로 설정한다. Foundry Agent에는 문서화된 다섯 도구를 연결하고 하이브리드 질문에서 두 근거를 합친다.

현재는 검색 인덱스, SQL Warehouse, 인증, 실제 LLM 합성이 없으므로 검색 관련성 평가와 최종 서비스 답변 생성은 불가능하다.
