from personal_qa.match_analysis import MatchAnalysisService, safe_rate


def _rows(count: int = 20) -> tuple[list[dict], list[dict]]:
    matches = []
    participants = []
    for index in range(count):
        match_id = f"MATCH_{index + 1:03d}"
        start = f"2026-09-{30 - index:02d}T00:00:00Z"
        matches.append({"match_id": match_id, "game_start_utc": start})
        participants.append(
            {
                "match_id": match_id,
                "player_id": "TARGET_PLAYER",
                "game_start_utc": start,
                "champion_name": "Malphite" if index == 0 else "Garen",
                "team_position": "TOP",
                "win": index % 2 == 0,
                "kda": float(index),
                "kills": index,
                "deaths": 1,
                "assists": 2,
                "cs_per_min": 5.0,
                "gold_per_min": 300.0,
                "damage_per_min": 400.0,
                "damage_taken_per_min": 500.0,
                "vision_per_min": 1.0,
                "objective_damage_per_min": 50.0,
                "team_id": 100,
            }
        )
    return matches, participants


def test_match_queries_and_recent_period_split() -> None:
    matches, participants = _rows()
    service = MatchAnalysisService(matches, participants)
    assert service.get_latest_champion_match("TARGET_PLAYER", "Malphite")["match_id"] == "MATCH_001"
    assert len(service.get_player_matches("TARGET_PLAYER", limit=20)) == 20
    comparison = service.compare_recent_periods("TARGET_PLAYER", 5, 15)
    assert comparison["status"] == "ok"
    assert comparison["recent_count"] == 5
    assert comparison["previous_count"] == 15


def test_safe_rate_and_missing_match() -> None:
    matches, participants = _rows(1)
    service = MatchAnalysisService(matches, participants)
    assert safe_rate(100, 0) is None
    assert safe_rate(100, 60) == 100.0
    assert service.get_match_summary("MATCH_999", "TARGET_PLAYER")["status"] == "unavailable"
