from __future__ import annotations

import os
from dataclasses import dataclass

WORKSPACE_ID = os.getenv("DATABRICKS_WORKSPACE_ID", "")
WORKSPACE_USER = os.getenv("DATABRICKS_USER", "")
WORKSPACE_RUNTIME_HOSTNAME = os.getenv("DATABRICKS_RUNTIME_HOSTNAME", "")
GIT_BRANCH = os.getenv("LOL_RAG_GIT_BRANCH", "feature/rag-personal-qa-prep")
GIT_HEAD = os.getenv("LOL_RAG_GIT_HEAD", "")
GIT_REPO_ID = os.getenv("DATABRICKS_GIT_REPO_ID", "")

RIOT_PLATFORM = "kr"
RIOT_REGIONAL_ROUTING = "asia"
SOLO_QUEUE_ID = 420
MAX_MATCH_COUNT = 20
REMAKE_SECONDS = 300
STANDARD_POSITIONS = {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}

SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")
SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX", "lol-official-rag-dev")
SEARCH_API_VERSION = "2024-07-01"
SEARCH_MAX_CALLS = 3
SEARCH_CONTEXT_LIMIT = 3

OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini-rag")
OPENAI_API_VERSION = "v1"
OPENAI_MAX_CALLS = 1

SECRET_SCOPE = os.getenv("DATABRICKS_SECRET_SCOPE", "lol-search-sp-dev")
ENTRA_SECRET_KEYS = ("tenant-id", "client-id", "client-secret")
RIOT_SECRET_KEY = "riot-api-key"


@dataclass(frozen=True)
class AgentLimits:
    match_count: int = MAX_MATCH_COUNT
    search_calls: int = SEARCH_MAX_CALLS
    openai_calls: int = OPENAI_MAX_CALLS


def normalize_match_count(value: int | str) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("match_count must be an integer") from exc
    if not 1 <= count <= MAX_MATCH_COUNT:
        raise ValueError(f"match_count must be between 1 and {MAX_MATCH_COUNT}")
    return count
