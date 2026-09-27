"""Thin Databricks widget/exit entrypoint for unified inference."""

from __future__ import annotations

import json
from typing import Any

from .unified_inference import run_unified_inference


def notebook_main(
    *,
    dbutils: Any,
    data_adapter: Any,
    llm_client: Any,
    playstyle_analyzer: Any | None = None,
) -> None:
    player_id = dbutils.widgets.get("player_id")
    recent_games_json = dbutils.widgets.get("recent_games_json")
    try:
        tier = dbutils.widgets.get("tier")
    except Exception:
        tier = "UNKNOWN"
    result = run_unified_inference(
        player_id=player_id,
        recent_games_json=recent_games_json,
        tier=tier or "UNKNOWN",
        data_adapter=data_adapter,
        llm_client=llm_client,
        playstyle_analyzer=playstyle_analyzer,
    )
    dbutils.notebook.exit(json.dumps(result, ensure_ascii=False, default=str))


__all__ = ["notebook_main"]
