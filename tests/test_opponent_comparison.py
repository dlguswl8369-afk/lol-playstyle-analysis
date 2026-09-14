from personal_qa.opponent_comparison import compare_with_position_opponent


def _participant(player_id: str, team_id: int, position: str, value: float) -> dict:
    return {
        "match_id": "MATCH_001",
        "player_id": player_id,
        "team_id": team_id,
        "team_position": position,
        "champion_name": player_id,
        "kda": value,
        "kills": value,
        "deaths": value,
        "assists": value,
        "cs_per_min": value,
        "gold_per_min": value,
        "damage_per_min": value,
        "damage_taken_per_min": value,
        "vision_per_min": value,
        "objective_damage_per_min": value,
    }


def test_matches_exactly_one_position_opponent() -> None:
    result = compare_with_position_opponent(
        [_participant("TARGET_PLAYER", 100, "TOP", 5), _participant("PLAYER_1", 200, "TOP", 3)],
        "MATCH_001",
        "TARGET_PLAYER",
    )
    assert result["opponent_match_status"] == "matched"
    assert result["opponent"]["player_id"] == "PLAYER_1"
    assert next(x for x in result["metric_comparisons"] if x["metric"] == "kda")["delta"] == 2


def test_marks_multiple_opponents_ambiguous() -> None:
    rows = [
        _participant("TARGET_PLAYER", 100, "TOP", 5),
        _participant("PLAYER_1", 200, "TOP", 3),
        _participant("PLAYER_2", 200, "TOP", 4),
    ]
    result = compare_with_position_opponent(rows, "MATCH_001", "TARGET_PLAYER")
    assert result["opponent_match_status"] == "ambiguous"
    assert result["reason"] == "opponent_count_2"


def test_does_not_guess_from_champion_when_position_missing() -> None:
    result = compare_with_position_opponent(
        [_participant("TARGET_PLAYER", 100, "", 5), _participant("PLAYER_1", 200, "TOP", 3)],
        "MATCH_001",
        "TARGET_PLAYER",
    )
    assert result["opponent_match_status"] == "unavailable"
