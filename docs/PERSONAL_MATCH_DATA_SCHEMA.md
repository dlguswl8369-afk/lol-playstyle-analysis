# 개인 경기 데이터 스키마와 원본 관계

## 조사 결과

| 데이터 | 실제 크기 | 관계와 사용 범위 |
| --- | --- | --- |
| `matches.csv` | 30행 × 94컬럼 | 특정 대상 사용자의 경기별 참가자 행 |
| 정제 XLSX `league_data_cleaned` | 180행 × 36컬럼 | 18경기 × 10명. `matches.csv`의 18경기와 파생·중복 관계 |
| ML 참가자 | 45,300행 × 63컬럼 | 4,530경기 × 10명. 개인 샘플과 단순 합산하지 않음 |
| ML 경기 목록 | 4,530행 × 1컬럼 | ML 참가자의 경기 집합 |
| ML 스타일 특성 | 1,006행 × 14컬럼 | 857명의 플레이어·포지션 집계 파생 데이터 |
| IRON Timeline | 585,070행 × 58컬럼 | 2,042경기, 58,507프레임. 이번 단계에서는 변환하지 않음 |

`matches.csv`와 XLSX의 겹치는 18경기는 `match_id + participant_id` 기준 핵심 지표가 모두 일치한다. 두 파일의 PUUID 표현은 일치하지 않으므로 PUUID로 조인하지 않는다. Timeline과 ML 경기 목록은 47경기만 겹친다.

## `matches`

`match_id`, `game_start_utc`, `game_duration_seconds`, `queue_id`, `map_id`, `game_version`, `is_remake`를 저장한다. 실제 Match ID는 `MATCH_NNN`으로 바꾼다.

## `match_participants`

다음 계약을 사용한다.

`match_id`, `player_id`, `participant_id`, `team_id`, `champion_id`, `champion_name`, `team_position`, `win`, `kills`, `deaths`, `assists`, `kda`, `cs`, `cs_per_min`, `gold_earned`, `gold_per_min`, `damage_to_champions`, `damage_per_min`, `damage_taken`, `damage_taken_per_min`, `vision_score`, `vision_per_min`, `objective_damage`, `objective_damage_per_min`, `is_target_player`.

로컬 JSONL에는 정렬용 `game_start_utc`와 누락 사유 객체 `unavailable_reasons`를 추가한다. 대상 사용자는 `TARGET_PLAYER`, 다른 참가자는 `PLAYER_NNNNN`이다. KDA와 모든 분당 지표는 원수치와 경기 시간으로 다시 계산한다.

30경기 중 18경기는 참가자 10명을 저장하고 12경기는 대상 사용자 한 명만 저장한다. 따라서 12경기의 상대 비교는 `unavailable`이다. XLSX에는 CS 컬럼이 없어 상대의 `cs`와 `cs_per_min`은 `null`이며 사유를 기록한다.

## `timeline_features`

향후 필드는 `match_id`, `player_id`, `participant_id`, `minute`, `level`, `xp`, `total_gold`, `current_gold`, `cs`, `position_x`, `position_y`이다.

IRON Timeline의 실제 키는 `matchId`, `participantId`, `frame.index`, `frame.timestamp`이다. `participantFrame.level`, `xp`, `totalGold`, `currentGold`, `minionsKilled`, `jungleMinionsKilled`, `position.x`, `position.y`를 사용한다. 10분과 15분 지표는 목표 시각 이하의 마지막 프레임을 선택하고, 정확한 프레임이 없으면 선택된 시각을 함께 기록한다. 동일 키가 중복되거나 목표 이전 프레임이 없으면 임의 보간하지 않고 `ambiguous` 또는 `unavailable`로 표시한다.

## `player_match_features`

대상과 같은 경기, 다른 팀, 같은 표준 포지션인 참가자가 정확히 한 명일 때만 상대를 연결한다. `match_id`, `player_id`, `champion_name`, `team_position`, `opponent_player_id`, `opponent_champion_name`, `kda_delta`, `cs_per_min_delta`, `gold_per_min_delta`, `damage_per_min_delta`, `vision_per_min_delta`, `objective_damage_per_min_delta`를 저장한다.

추가 상태 필드 `opponent_match_status`는 `matched`, `unavailable`, `ambiguous` 중 하나이며 `opponent_match_reason`에 사유를 기록한다. 챔피언 이름만으로 상대를 추정하지 않는다.
