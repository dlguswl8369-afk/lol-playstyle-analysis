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
