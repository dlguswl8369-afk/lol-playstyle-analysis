# RAG 검색 문서 스키마

`data/processed/rag_search/rag_documents.jsonl`은 한 줄에 문서 하나를 저장한다. 필드 순서는 다음과 같다.

| 필드 | 자료형 | 설명 |
| --- | --- | --- |
| `document_id` | string | 원본에서 보존한 고유 문서 ID |
| `document_type` | enum | champion, item, rune, summoner_spell, patch_note, season, queue, map, game_mode, game_type |
| `title` | string | 검색 결과 제목. 빈 값 불가 |
| `content` | string | 원본 검색 본문. 축약하거나 다시 쓰지 않음 |
| `source_url` | string 또는 null | 원본 공식 출처 |
| `locale` | string | `ko_KR` |
| `version` | string 또는 null | Data Dragon 버전 |
| `patch_version` | string 또는 null | 패치노트 버전 |
| `category` | string 또는 null | 원본 분류 |
| `target_name` | string 또는 null | 패치 변경 대상 이름 |
| `applicable_modes` | array | 원본 모드 목록. 없으면 빈 배열 |
| `source_id` | string 또는 number 또는 null | Riot 공식 ID |
| `metadata` | object | 공통 필드 밖의 원본 메타데이터 |

## 문서 수 계약

| 유형 | 수량 |
| --- | ---: |
| champion | 173 |
| item | 254 |
| rune | 62 |
| summoner_spell | 34 |
| patch_note | 63 |
| season | 14 |
| queue | 99 |
| map | 17 |
| game_mode | 22 |
| game_type | 3 |
| 합계 | 741 |

`document_id` 중복, 빈 제목, 빈 본문은 허용하지 않는다. `rag_documents_manifest.json`에는 유형별 수량, 출력 크기와 SHA-256, 필드 순서를 기록한다. `validation_report.json`에는 UTF-8·JSONL·버전 분리 검증 결과를 기록한다.

Data Dragon `16.18.1`과 패치노트 `26.18`, `26.17`, `26.16`은 서로 다른 원본 버전 체계를 그대로 유지한다.

## 키워드 검색 전처리

`game_mode`와 `game_type`은 Riot 기준정보의 영문 식별자를 유지한다. 원본 문서나 Search
인덱스를 번역해 변경하지 않고, 검색 요청에서만 다음 최소 별칭을 사용한다.

- `소환사의 협곡`, `협곡` + `게임 모드` → `Summoner's Rift`, `document_type eq 'game_mode'`
- `협곡` + `게임 타입` → `document_type eq 'game_type'`으로 후보만 조회하고
  `needs_clarification`을 반환

이 별칭은 검색 편의를 위한 서비스 내부 규칙이며 Riot의 공식 한국어 번역으로 표시하지 않는다.
Map은 전장, Queue는 매칭 대기열과 규칙, Game Mode는 진행 방식, Game Type은 일반 매칭·
사용자 설정·튜토리얼 구분으로 서로 다른 축이다.

### 소환사 주문 모드 우선순위

Data Dragon `16.18.1`의 점멸 변형을 확인한 결과, 최상위 `applicable_modes`는 세 문서 모두
빈 배열이고 원본 `metadata.modes`에만 모드가 기록되어 있다. 따라서 현재 v1 인덱스에서
`applicable_modes/any(mode: mode eq 'CLASSIC')` 필터는 사용할 수 없다.

- 모드 미지정 또는 일반 질문: `CLASSIC`의 공식 `source_id=SummonerFlash`
- `CHERRY` 또는 `아레나` 명시: `source_id=SummonerCherryFlash`
- `JADE` 명시: `source_id=SummonerFlash_Jade`

점멸 단일 질문에는 필터 가능한 공식 `source_id`를 함께 적용한다. 이 방식은 일반 질문에서
특수 모드 변형을 근거로 선택하지 않으며, 해당 source ID가 없으면 검색 결과 0건을
`no_result`로 처리한다. 원본 JSONL과 Search 인덱스는 변경하지 않는다.
