# QA 평가 질문 40개

각 질문은 라우터 유형, 사용할 도구, 필요한 데이터, 기대 근거, 답변 불가능 조건, 개인정보 노출 정책을 함께 기록한다. 모든 행의 개인정보 정책은 실제 플레이어 식별자 비노출이다.

## 공식 게임 정보 15개

| 번호 | 질문 | 유형 | 도구 | 데이터·기대 근거 | 불가능 조건 | 개인정보 |
| ---: | --- | --- | --- | --- | --- | --- |
| K01 | 말파이트는 어떤 챔피언이야? | knowledge_query | search_game_knowledge | champion 문서의 역할·능력치·스킬 | 문서 없음 | 없음 |
| K02 | 아이템 6655가 뭐야? | knowledge_query | search_game_knowledge | source_id 6655의 이름·효과 | ID 없음 | 없음 |
| K03 | Queue ID 420이 뭐야? | knowledge_query | search_game_knowledge | queue 420 설명 | Queue 문서 없음 | 없음 |
| K04 | 최근 패치에서 말파이트가 변경됐어? | knowledge_query | search_game_knowledge | 패치 버전별 target_name | 최근 3개 문서에 없음 | 없음 |
| K05 | 점멸 소환사 주문의 효과가 뭐야? | knowledge_query | search_game_knowledge | summoner_spell 본문 | 문서 없음 | 없음 |
| K06 | 착취의 손아귀 룬 효과를 알려줘. | knowledge_query | search_game_knowledge | rune 본문 | 문서 없음 | 없음 |
| K07 | Map ID 11은 어떤 맵이야? | knowledge_query | search_game_knowledge | map 기준표 | Map 문서 없음 | 없음 |
| K08 | 26.18 패치에서 상향된 챔피언은? | knowledge_query | search_game_knowledge | patch_note category·본문 | 버전 없음 | 없음 |
| K09 | 26.17 아이템 변경 사항을 알려줘. | knowledge_query | search_game_knowledge | 해당 패치 item 문서 | 버전 없음 | 없음 |
| K10 | 소환사의 협곡에서 쓰는 게임 모드는? | knowledge_query | search_game_knowledge | map·game_mode 문서 | 모드 연결 근거 없음 | 없음 |
| K11 | 시즌 코드 13의 의미는? | knowledge_query | search_game_knowledge | season 기준표 | 코드 없음 | 없음 |
| K12 | 텔레포트와 점멸 효과를 비교해줘. | knowledge_query | search_game_knowledge | 두 주문 공식 본문 | 하나라도 없음 | 없음 |
| K13 | 말파이트 스킬 설명을 알려줘. | knowledge_query | search_game_knowledge | champion 스킬 metadata | 스킬 없음 | 없음 |
| K14 | 협곡 게임 타입은 무엇이야? | knowledge_query | search_game_knowledge | game_type 문서 | 문서 없음 | 없음 |
| K15 | 최근 세 패치의 시스템 변경을 찾아줘. | knowledge_query | search_game_knowledge | patch_note category와 버전 | 패치 문서 부족 | 없음 |

### K10·K14 키워드 평가 규칙

- K10은 `game_mode`로 분류하고 검증된 공식 영문 구문 `Summoner's Rift`로 검색한다.
  이 검색은 `CLASSIC`을 유일한 정답으로 강제하지 않는다.
- K14는 `game_type`로 분류하지만 Map 정보만으로 `MATCHED_GAME`, `CUSTOM_GAME`,
  `TUTORIAL_GAME`을 확정할 수 없으므로 `needs_clarification`이 기대 결과다.

### K05 소환사 주문 모드 평가 규칙

- 모드를 명시하지 않은 점멸 질문은 프로젝트 기본 범위인 `CLASSIC`의
  `source_id=SummonerFlash`를 선택한다.
- `CHERRY`·`아레나` 또는 `JADE`가 명시되면 각각 검증된 모드별 source ID를 선택한다.
- 일반 점멸 답변의 근거로 `SummonerCherryFlash`나 `SummonerFlash_Jade`를 사용하지 않는다.

## 개인 경기 분석 15개

| 번호 | 질문 | 유형 | 도구 | 데이터·기대 근거 | 불가능 조건 | 개인정보 |
| ---: | --- | --- | --- | --- | --- | --- |
| P01 | 내 이번 말파이트 경기 어땠어? | personal_match_query | get_player_matches, get_match_summary | 최신 말파이트 경기 수치 | 해당 경기 없음 | 익명 ID만 |
| P02 | 상대 탑보다 뭘 못했어? | personal_match_query | compare_with_position_opponent | 같은 포지션 지표 delta | 상대 불명확 | 상대 익명 |
| P03 | 내가 이번 경기에서 잘한 점은 뭐야? | personal_match_query | get_match_summary, compare_with_position_opponent | 양호 방향 수치 차이 | 상대 없음 | 익명 ID만 |
| P04 | 최근 5경기와 이전 15경기가 어떻게 달라졌어? | personal_match_query | compare_recent_periods | 5·15경기 평균 delta | 20경기 미만 | 익명 ID만 |
| P05 | 내 시야 점수가 같은 포지션 상대보다 낮았어? | personal_match_query | compare_with_position_opponent | vision_per_min delta | 상대·시야 없음 | 상대 익명 |
| P06 | 이번 경기에서 골드와 피해량 차이가 얼마나 났어? | personal_match_query | compare_with_position_opponent | gold_per_min·damage_per_min | 상대 없음 | 상대 익명 |
| P07 | 최근 10경기 승률은? | personal_match_query | get_player_matches | win 평균과 표본 수 | 10경기 미만 | 익명 ID만 |
| P08 | 내 데스가 상대 미드보다 많았어? | personal_match_query | compare_with_position_opponent | deaths delta | 포지션 불일치 | 상대 익명 |
| P09 | 최근 경기 CS/분 추세를 알려줘. | personal_match_query | get_player_matches | 경기별 cs_per_min | CS 없음 | 익명 ID만 |
| P10 | 오브젝트 피해가 가장 높았던 내 경기는? | personal_match_query | get_player_matches | objective_damage 정렬 | 값 없음 | 익명 ID만 |
| P11 | 내가 정글로 한 경기만 보여줘. | personal_match_query | get_player_matches | team_position 필터 | 포지션 없음 | 익명 ID만 |
| P12 | 내 최근 말파이트 승패를 보여줘. | personal_match_query | get_player_matches | champion 필터·win | 경기 없음 | 익명 ID만 |
| P13 | 이번 경기가 리메이크였어? | personal_match_query | get_match_summary | duration·is_remake | 경기 없음 | 익명 ID만 |
| P14 | 상대보다 받은 피해가 얼마나 달랐어? | personal_match_query | compare_with_position_opponent | damage_taken_per_min | 상대 없음 | 상대 익명 |
| P15 | 최근 경기들의 KDA 평균은? | personal_match_query | get_player_matches | 재계산 KDA 평균 | 경기 없음 | 익명 ID만 |

## 혼합 질문 10개

| 번호 | 질문 | 유형 | 도구 | 데이터·기대 근거 | 불가능 조건 | 개인정보 |
| ---: | --- | --- | --- | --- | --- | --- |
| H01 | 내 경기 결과를 바탕으로 말파이트 관련 공식 정보를 알려줘. | hybrid_query | get_match_summary, search_game_knowledge | 경기 챔피언과 공식 champion 문서 | 한쪽 근거 없음 | 익명 ID만 |
| H02 | 내 최근 말파이트 경기와 26.18 변경점을 같이 보여줘. | hybrid_query | get_player_matches, search_game_knowledge | 경기 수치·patch_note | 경기나 패치 없음 | 익명 ID만 |
| H03 | 내 아이템 6655 사용 경기와 공식 아이템 효과를 설명해줘. | hybrid_query | get_player_matches, search_game_knowledge | 경기 item·item 문서 | 아이템 필드 없음 | 익명 ID만 |
| H04 | 내 Queue 420 경기 성적과 이 Queue 설명을 알려줘. | hybrid_query | get_player_matches, search_game_knowledge | queue 필터·queue 문서 | Queue 근거 없음 | 익명 ID만 |
| H05 | 내 시야가 낮았던 경기와 관련 룬의 공식 효과를 보여줘. | hybrid_query | get_player_matches, search_game_knowledge | vision·rune 문서 | 사용 룬 데이터 없음 | 익명 ID만 |
| H06 | 내 말파이트 상대 비교와 공식 스킬 정보를 함께 알려줘. | hybrid_query | compare_with_position_opponent, search_game_knowledge | delta·champion 스킬 | 상대 없음 | 상대 익명 |
| H07 | 최근 패치 챔피언 중 내가 플레이한 챔피언이 있어? | hybrid_query | get_player_matches, search_game_knowledge | champion 교집합 | 표본·패치 없음 | 익명 ID만 |
| H08 | 내 소환사 주문과 그 공식 효과를 같이 보여줘. | hybrid_query | get_match_summary, search_game_knowledge | 주문 ID·spell 문서 | 주문 데이터 없음 | 익명 ID만 |
| H09 | 내 소환사의 협곡 경기 통계와 Map 11 정보를 알려줘. | hybrid_query | get_player_matches, search_game_knowledge | map 필터·map 문서 | Map 근거 없음 | 익명 ID만 |
| H10 | 내 최근 5경기 변화와 관련된 26.18 공식 변경을 찾아줘. | hybrid_query | compare_recent_periods, search_game_knowledge | 기간 delta·patch 문서 | 인과 근거 없음 | 익명 ID만 |
