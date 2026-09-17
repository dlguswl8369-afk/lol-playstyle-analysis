from .client import RiotAPIClient, RiotAPIError
from .collector import RiotMatchCollector
from .config import Settings, load_settings
from .parser import LEAGUE_DATA_COLUMNS, build_league_rows, validate_league_rows

__all__ = [
    "RiotAPIClient",
    "RiotAPIError",
    "RiotMatchCollector",
    "export_workbook",
    "LEAGUE_DATA_COLUMNS",
    "Settings",
    "build_league_rows",
    "load_settings",
    "validate_league_rows",
]


def __getattr__(name: str):
    if name == "export_workbook":
        from .excel_exporter import export_workbook

        return export_workbook
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
