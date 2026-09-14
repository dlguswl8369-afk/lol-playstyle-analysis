from __future__ import annotations

from typing import Any

ERROR_SCHEMA = {
    "type": "object",
    "required": ["status", "error"],
    "properties": {
        "status": {"const": "error"},
        "error": {
            "type": "object",
            "required": ["code", "message"],
            "properties": {
                "code": {
                    "enum": [
                        "invalid_request",
                        "not_found",
                        "insufficient_data",
                        "ambiguous_opponent",
                        "backend_unavailable",
                    ]
                },
                "message": {"type": "string"},
                "details": {"type": "object"},
            },
        },
    },
}

TOOL_CONTRACTS: dict[str, dict[str, Any]] = {
    "search_game_knowledge": {
        "description": "등록된 Riot 공식 지식 문서를 검색합니다.",
        "input_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "document_types": {"type": "array", "items": {"type": "string"}},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            },
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "required": ["status", "results"],
            "properties": {
                "status": {"const": "ok"},
                "results": {"type": "array", "items": {"type": "object"}},
            },
        },
    },
    "get_player_matches": {
        "description": "익명 플레이어의 최근 경기 목록을 조회합니다.",
        "input_schema": {
            "type": "object",
            "required": ["player_id"],
            "properties": {
                "player_id": {"type": "string"},
                "champion_name": {"type": ["string", "null"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "additionalProperties": False,
        },
        "output_schema": {"type": "object", "required": ["status", "matches"]},
    },
    "get_match_summary": {
        "description": "한 경기에서 익명 플레이어의 구조화된 통계를 조회합니다.",
        "input_schema": {
            "type": "object",
            "required": ["match_id", "player_id"],
            "properties": {"match_id": {"type": "string"}, "player_id": {"type": "string"}},
            "additionalProperties": False,
        },
        "output_schema": {"type": "object", "required": ["status"]},
    },
    "compare_with_position_opponent": {
        "description": "같은 포지션의 상대가 정확히 한 명일 때 지표 차이를 계산합니다.",
        "input_schema": {
            "type": "object",
            "required": ["match_id", "player_id"],
            "properties": {"match_id": {"type": "string"}, "player_id": {"type": "string"}},
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "required": ["opponent_match_status", "metric_comparisons", "limitations"],
        },
    },
    "compare_recent_periods": {
        "description": "최근 구간과 직전 구간의 평균 통계 차이를 계산합니다.",
        "input_schema": {
            "type": "object",
            "required": ["player_id"],
            "properties": {
                "player_id": {"type": "string"},
                "recent_count": {"type": "integer", "minimum": 1, "default": 5},
                "previous_count": {"type": "integer", "minimum": 1, "default": 15},
            },
            "additionalProperties": False,
        },
        "output_schema": {"type": "object", "required": ["status"]},
    },
}


def get_tool_contract(name: str) -> dict[str, Any]:
    try:
        return TOOL_CONTRACTS[name]
    except KeyError as exc:
        raise ValueError(f"unknown tool: {name}") from exc
