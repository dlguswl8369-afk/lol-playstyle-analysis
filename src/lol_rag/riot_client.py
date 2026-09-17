from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from riot_api.client import RiotAPIClient, RiotAPIError

from .config import MAX_MATCH_COUNT, RIOT_PLATFORM, RIOT_REGIONAL_ROUTING, SOLO_QUEUE_ID


class RiotApiError(RuntimeError):
    def __init__(
        self,
        code: str,
        stage: str,
        http_status: int | None = None,
        exception_class: str | None = None,
        inner_exception_class: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.stage = stage
        self.http_status = http_status
        self.exception_class = exception_class
        self.inner_exception_class = inner_exception_class


@dataclass
class RiotUsage:
    calls: int = 0


class RiotClient:
    """Minimal, no-retry Riot client restricted to KR solo ranked data."""

    def __init__(
        self,
        api_key: str,
        timeout_seconds: int = 20,
        *,
        trust_env: bool = True,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("riot_api_key is required")
        self._client = RiotAPIClient(
            api_key,
            timeout=float(timeout_seconds),
            request_interval=0.0,
            max_retries=0,
            trust_env=trust_env,
            transport=transport,
        )
        self.usage = RiotUsage()

    def close(self) -> None:
        self._client.close()

    def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        stage: str,
    ) -> Any:
        self.usage.calls += 1
        try:
            return self._client.get_json(url, params=params)
        except RiotAPIError as exc:
            if exc.status_code == 400:
                safe_code = "RIOT_BAD_REQUEST"
            elif exc.status_code == 401:
                safe_code = "RIOT_API_UNAUTHORIZED"
            elif exc.status_code == 403:
                safe_code = "RIOT_API_KEY_EXPIRED_OR_FORBIDDEN"
            elif exc.status_code == 404:
                safe_code = "RIOT_ACCOUNT_NOT_FOUND"
            elif exc.status_code == 429:
                safe_code = "RIOT_RATE_LIMITED"
            elif exc.status_code is not None and 500 <= exc.status_code < 600:
                safe_code = "RIOT_SERVICE_ERROR"
            elif exc.status_code is None:
                classes = set(exc.exception_classes)
                if "LocalProtocolError" in classes:
                    safe_code = "RIOT_LOCAL_PROTOCOL_ERROR"
                elif "InvalidURL" in classes:
                    safe_code = "RIOT_INVALID_URL"
                elif "ConnectTimeout" in classes:
                    safe_code = "RIOT_CONNECT_TIMEOUT"
                elif "ReadTimeout" in classes:
                    safe_code = "RIOT_READ_TIMEOUT"
                elif "RemoteProtocolError" in classes:
                    safe_code = "RIOT_REMOTE_PROTOCOL_ERROR"
                elif classes.intersection({"SSLError", "SSLCertVerificationError"}):
                    safe_code = "RIOT_TLS_ERROR"
                elif "gaierror" in classes:
                    safe_code = "RIOT_DNS_ERROR"
                elif "ConnectError" in classes:
                    safe_code = "RIOT_CONNECT_ERROR"
                else:
                    safe_code = "RIOT_NETWORK_ERROR"
            else:
                safe_code = "RIOT_API_ERROR"
            raise RiotApiError(
                safe_code,
                stage,
                exc.status_code,
                exc.exception_class,
                exc.inner_exception_class,
            ) from None

    @property
    def platform_host(self) -> str:
        return f"{RIOT_PLATFORM}.api.riotgames.com"

    @property
    def regional_host(self) -> str:
        return f"{RIOT_REGIONAL_ROUTING}.api.riotgames.com"

    def account_by_riot_id(self, riot_id: str, tag_line: str) -> dict[str, Any]:
        riot_id = riot_id.strip()
        tag_line = tag_line.strip().removeprefix("#")
        encoded_riot_id = quote(riot_id, safe="")
        encoded_tag_line = quote(tag_line, safe="")
        return self._get(
            "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/"
            f"{encoded_riot_id}/{encoded_tag_line}",
            stage="account_v1",
        )

    def summoner_by_puuid(self, puuid: str) -> dict[str, Any]:
        value = quote(puuid, safe="")
        return self._get(
            f"https://{self.platform_host}/lol/summoner/v4/summoners/by-puuid/{value}",
            stage="summoner_v4",
        )

    def solo_rank(self, summoner_id: str) -> dict[str, Any] | None:
        value = quote(summoner_id, safe="")
        entries = self._get(
            f"https://{self.platform_host}/lol/league/v4/entries/by-summoner/{value}",
            stage="league_v4",
        )
        return next(
            (entry for entry in entries if entry.get("queueType") == "RANKED_SOLO_5x5"),
            None,
        )

    def recent_solo_match_ids(self, puuid: str, count: int) -> list[str]:
        bounded_count = max(1, min(int(count), MAX_MATCH_COUNT))
        value = quote(puuid, safe="")
        result = self._get(
            f"https://{self.regional_host}/lol/match/v5/matches/by-puuid/{value}/ids",
            {"queue": SOLO_QUEUE_ID, "start": 0, "count": bounded_count},
            stage="match_v5_ids",
        )
        return [str(match_id) for match_id in result[:bounded_count]]

    def match(self, match_id: str) -> dict[str, Any]:
        value = quote(match_id, safe="")
        return self._get(
            f"https://{self.regional_host}/lol/match/v5/matches/{value}",
            stage="match_v5",
        )

    def timeline(self, match_id: str) -> dict[str, Any]:
        value = quote(match_id, safe="")
        return self._get(
            f"https://{self.regional_host}/lol/match/v5/matches/{value}/timeline",
            stage="match_v5_timeline",
        )
