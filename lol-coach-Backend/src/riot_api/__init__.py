"""Minimal Riot API surface required by the FastAPI RAG integration bundle."""

from .client import RiotAPIClient, RiotAPIError

__all__ = ["RiotAPIClient", "RiotAPIError"]
