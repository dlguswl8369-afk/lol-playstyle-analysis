from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"


def test_core_riot_imports_do_not_require_openpyxl() -> None:
    script = """
import builtins

real_import = builtins.__import__

def without_openpyxl(name, *args, **kwargs):
    if name == "openpyxl" or name.startswith("openpyxl."):
        raise ModuleNotFoundError("blocked openpyxl for import isolation test")
    return real_import(name, *args, **kwargs)

builtins.__import__ = without_openpyxl

from riot_api.client import RiotAPIClient as DirectClient
from riot_api import RiotAPIClient, RiotMatchCollector

assert DirectClient is RiotAPIClient
assert RiotMatchCollector.__name__ == "RiotMatchCollector"
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    subprocess.run([sys.executable, "-c", script], env=env, check=True)


def test_export_workbook_remains_available() -> None:
    from riot_api import export_workbook

    assert callable(export_workbook)
