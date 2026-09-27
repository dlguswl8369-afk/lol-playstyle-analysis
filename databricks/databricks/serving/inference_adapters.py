"""External dependency adapters for unified inference."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol


class AnalysisDataAdapter(Protocol):
    def get_item_role_map(self) -> Mapping[int, str]: ...
    def get_champion_profiles(
        self, champion_names: Iterable[str]
    ) -> Mapping[str, Mapping[str, Any]]: ...
    def get_benchmark_rows(self) -> Sequence[Mapping[str, Any]]: ...
    def get_rf_importance_rows(self) -> Sequence[Mapping[str, Any]]: ...
    def get_direction_rows(self) -> Sequence[Mapping[str, Any]]: ...


class LLMClient(Protocol):
    def generate(self, *, system_prompt: str, user_prompt: str) -> str: ...


class DatabricksDeltaAdapter:
    """Read-only adapter for the four v8.2 serving tables plus item roles."""

    def __init__(self, spark_session: Any, catalog: str = "lol_insight"):
        self.spark = spark_session
        self.catalog = catalog

    @staticmethod
    def _records(rows: Sequence[Any]) -> list[dict[str, Any]]:
        return [row.asDict(recursive=True) if hasattr(row, "asDict") else dict(row) for row in rows]

    def _table(self, suffix: str):
        return self.spark.table(f"{self.catalog}.gold.{suffix}")

    def get_item_role_map(self) -> dict[int, str]:
        rows = self._records(self._table("item_role_profile").select("item_id", "roles").collect())
        return {int(row["item_id"]): str(row["roles"]) for row in rows}

    def get_champion_profiles(self, champion_names: Iterable[str]) -> dict[str, dict[str, Any]]:
        names = sorted({str(name) for name in champion_names if name})
        if not names:
            return {}
        frame = self._table("champion_profile_data_driven").select(
            "champion_name", "default_tactical_role", "power_curve"
        )
        rows = self._records(frame.filter(frame.champion_name.isin(names)).collect())
        return {
            str(row["champion_name"]): {
                "default_tactical_role": row.get("default_tactical_role"),
                "power_curve": row.get("power_curve"),
            }
            for row in rows
        }

    def get_benchmark_rows(self) -> list[dict[str, Any]]:
        return self._records(self._table("tier_position_benchmark").collect())

    def get_rf_importance_rows(self) -> list[dict[str, Any]]:
        return self._records(self._table("role_metric_rf_importance").collect())

    def get_direction_rows(self) -> list[dict[str, Any]]:
        return self._records(self._table("role_metric_direction").collect())


__all__ = ["AnalysisDataAdapter", "DatabricksDeltaAdapter", "LLMClient"]
