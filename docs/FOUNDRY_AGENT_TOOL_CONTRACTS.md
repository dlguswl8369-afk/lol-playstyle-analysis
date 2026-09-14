# Foundry Agent 도구 계약

실제 Foundry Agent는 만들지 않는다. Python의 `src/personal_qa/tool_contracts.py`가 입력·출력 JSON Schema의 기준이다.

## 도구

| 도구 | 입력 | 성공 출력 | 주요 오류 |
| --- | --- | --- | --- |
| `search_game_knowledge` | `query`, 선택적 `document_types`, `top_k` | 공식 문서 결과 배열 | invalid_request, backend_unavailable |
| `get_player_matches` | `player_id`, 선택적 `champion_name`, `limit` | 익명 최근 경기 배열 | not_found, insufficient_data |
| `get_match_summary` | `match_id`, `player_id` | 경기와 대상 참가자 통계 | not_found, insufficient_data |
| `compare_with_position_opponent` | `match_id`, `player_id` | 상대 상태와 지표 차이 | ambiguous_opponent, insufficient_data |
| `compare_recent_periods` | `player_id`, `recent_count`, `previous_count` | 두 구간 평균과 차이 | insufficient_data |

## 공통 오류

```json
{
  "status": "error",
  "error": {
    "code": "insufficient_data",
    "message": "동일 포지션 상대 데이터가 없습니다.",
    "details": {}
  }
}
```

오류 코드는 `invalid_request`, `not_found`, `insufficient_data`, `ambiguous_opponent`, `backend_unavailable`로 제한한다. 오류 메시지에 실제 Riot ID, 태그, PUUID, Summoner ID, Account ID를 포함하지 않는다.

## 조합 규칙

`knowledge_query`는 `search_game_knowledge`, `personal_match_query`는 경기 도구만 호출한다. `hybrid_query`는 경기 근거를 먼저 만든 뒤 필요한 챔피언·아이템·패치 문서를 검색한다. 한쪽 근거가 없으면 다른 쪽 결과만으로 없는 내용을 추측하지 않고 한계를 명시한다.
