"""Validated serving entry point for the ml-training playstyle engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .playstyle_explain_v2 import ExplainablePlayStyleAnalyzer as _ExplainableAnalyzer

EXPECTED_ANALYSIS_GAMES = 10
EXPECTED_QUEUE_FILTER = 420
FORBIDDEN_STANDALONE_METRIC = "damage_taken_per_min"


class ArtifactCompatibilityError(ValueError):
    """Raised when training and serving expectations are incompatible."""


def validate_style_artifact(
    artifact: dict[str, Any],
    expected_analysis_games: int = EXPECTED_ANALYSIS_GAMES,
    expected_queue_filter: int = EXPECTED_QUEUE_FILTER,
) -> dict[str, Any]:
    required = {
        "version",
        "artifact_type",
        "authoritative_source",
        "analysis_games",
        "queue_filter",
        "config",
        "reliability",
        "stats",
        "fallback_stats",
        "centroids",
        "labels",
        "style_baseline",
        "feature_labels",
        "explain",
    }
    missing = sorted(required - artifact.keys())
    if missing:
        raise ArtifactCompatibilityError(
            "style_model.json is missing required fields: " + ", ".join(missing)
        )

    config = artifact.get("config") or {}
    top_games = artifact.get("analysis_games")
    config_games = config.get("analysis_games")
    if top_games != config_games:
        raise ArtifactCompatibilityError(
            f"style_model.json has inconsistent analysis_games: top-level={top_games}, "
            f"config={config_games}"
        )
    if config_games != expected_analysis_games:
        raise ArtifactCompatibilityError(
            f"style_model.json analysis_games={config_games}, but serving expects "
            f"{expected_analysis_games}; retrain the artifact before serving"
        )

    top_queue = artifact.get("queue_filter")
    config_queue = config.get("queue_filter")
    if top_queue != config_queue or config_queue != expected_queue_filter:
        raise ArtifactCompatibilityError(
            f"style_model.json queue_filter mismatch: top-level={top_queue}, "
            f"config={config_queue}, expected={expected_queue_filter}"
        )
    if artifact.get("artifact_type") != "explainable_playstyle":
        raise ArtifactCompatibilityError("Unsupported playstyle artifact_type")
    return artifact


def load_style_artifact(
    path: str | Path,
    expected_analysis_games: int = EXPECTED_ANALYSIS_GAMES,
) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        artifact = json.load(handle)
    return validate_style_artifact(artifact, expected_analysis_games)


def enforce_feedback_safety(result: dict[str, Any]) -> dict[str, Any]:
    """Prevent damage taken from becoming standalone evaluative feedback."""
    sanitized = dict(result)
    for key in ("strengths", "improvements"):
        sanitized[key] = [
            item
            for item in sanitized.get(key, [])
            if item.get("key") != FORBIDDEN_STANDALONE_METRIC
        ]
    primary = sanitized.get("primary_goal")
    if isinstance(primary, dict) and primary.get("key") == FORBIDDEN_STANDALONE_METRIC:
        sanitized["primary_goal"] = None
    trend = dict(sanitized.get("recent_trend") or {})
    trend["changes"] = [
        item for item in trend.get("changes", []) if item.get("key") != FORBIDDEN_STANDALONE_METRIC
    ]
    if trend and trend.get("status") == "changed" and not trend["changes"]:
        trend["status"] = "stable"
        trend["note"] = "독립적으로 해석할 수 있는 임계값 이상 변화가 없습니다."
    if trend:
        sanitized["recent_trend"] = trend
    return sanitized


class ExplainablePlayStyleAnalyzer(_ExplainableAnalyzer):
    """The only supported final-serving playstyle analyzer."""

    def __init__(
        self,
        artifact: dict[str, Any],
        expected_analysis_games: int = EXPECTED_ANALYSIS_GAMES,
    ):
        validated = validate_style_artifact(artifact, expected_analysis_games)
        super().__init__(validated)

    def analyze(self, games: list[dict[str, Any]]) -> dict[str, Any]:
        return enforce_feedback_safety(super().analyze(games))


def create_playstyle_analyzer(
    artifact_path: str | Path | None = None,
    expected_analysis_games: int = EXPECTED_ANALYSIS_GAMES,
) -> ExplainablePlayStyleAnalyzer:
    path = Path(artifact_path) if artifact_path else Path(__file__).with_name("style_model.json")
    if not path.is_file():
        raise FileNotFoundError(
            f"Required trained playstyle artifact not found: {path}. "
            "Run databricks/offline/playstyle/04_ml_training_v8.py first."
        )
    artifact = load_style_artifact(path, expected_analysis_games)
    return ExplainablePlayStyleAnalyzer(artifact, expected_analysis_games)


__all__ = [
    "ArtifactCompatibilityError",
    "EXPECTED_ANALYSIS_GAMES",
    "ExplainablePlayStyleAnalyzer",
    "create_playstyle_analyzer",
    "enforce_feedback_safety",
    "load_style_artifact",
    "validate_style_artifact",
]
