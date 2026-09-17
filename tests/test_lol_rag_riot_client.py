from __future__ import annotations

import httpx
import pytest

from lol_rag.riot_client import RiotApiError, RiotClient
from riot_api.client import RiotAPIError


def _success_transport(requests: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"puuid": "test-puuid"})

    return httpx.MockTransport(handler)


def test_account_path_encodes_korean_as_ascii() -> None:
    requests: list[httpx.Request] = []
    client = RiotClient("test-key", transport=_success_transport(requests))
    try:
        client.account_by_riot_id("한글아이디", "KR1")
    finally:
        client.close()

    assert requests[0].url.host == "asia.api.riotgames.com"
    requests[0].url.raw_path.decode("ascii")
    assert "%ED%95%9C%EA%B8%80%EC%95%84%EC%9D%B4%EB%94%94" in str(requests[0].url)
    assert requests[0].url.query == b""


def test_account_path_strips_hash_from_tag_line() -> None:
    requests: list[httpx.Request] = []
    client = RiotClient("test-key", transport=_success_transport(requests))
    try:
        client.account_by_riot_id("Player", "  #KR1  ")
    finally:
        client.close()

    assert requests[0].url.path.endswith("/Player/KR1")
    assert "%23" not in str(requests[0].url)


def test_account_path_encodes_spaces() -> None:
    requests: list[httpx.Request] = []
    client = RiotClient("test-key", transport=_success_transport(requests))
    try:
        client.account_by_riot_id(" Player Name ", "KR1")
    finally:
        client.close()

    assert "/Player%20Name/KR1" in str(requests[0].url)


def test_account_bad_request_has_safe_error_metadata() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"status": {"message": "ignored"}})

    client = RiotClient("test-key", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RiotApiError) as exc_info:
            client.account_by_riot_id("Player", "KR1")
    finally:
        client.close()

    assert exc_info.value.code == "RIOT_BAD_REQUEST"
    assert exc_info.value.stage == "account_v1"
    assert exc_info.value.http_status == 400
    assert client.usage.calls == 1


@pytest.mark.parametrize(
    ("http_status", "expected_code"),
    [
        (401, "RIOT_API_UNAUTHORIZED"),
        (403, "RIOT_API_KEY_EXPIRED_OR_FORBIDDEN"),
        (404, "RIOT_ACCOUNT_NOT_FOUND"),
        (429, "RIOT_RATE_LIMITED"),
        (500, "RIOT_SERVICE_ERROR"),
        (503, "RIOT_SERVICE_ERROR"),
    ],
)
def test_account_http_errors_have_safe_metadata(
    http_status: int,
    expected_code: str,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(http_status, json={"status": {"message": "ignored"}})

    client = RiotClient("test-key", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RiotApiError) as exc_info:
            client.account_by_riot_id("Player", "KR1")
    finally:
        client.close()

    assert exc_info.value.code == expected_code
    assert exc_info.value.stage == "account_v1"
    assert exc_info.value.http_status == http_status
    assert client.usage.calls == 1


@pytest.mark.parametrize(
    ("exception_classes", "expected_code"),
    [
        (("LocalProtocolError",), "RIOT_LOCAL_PROTOCOL_ERROR"),
        (("InvalidURL",), "RIOT_INVALID_URL"),
        (("ConnectTimeout",), "RIOT_CONNECT_TIMEOUT"),
        (("ReadTimeout",), "RIOT_READ_TIMEOUT"),
        (("ConnectError",), "RIOT_CONNECT_ERROR"),
        (("RemoteProtocolError",), "RIOT_REMOTE_PROTOCOL_ERROR"),
        (("ConnectError", "SSLError"), "RIOT_TLS_ERROR"),
        (("ConnectError", "gaierror"), "RIOT_DNS_ERROR"),
    ],
)
def test_network_errors_have_safe_specific_codes(
    monkeypatch: pytest.MonkeyPatch,
    exception_classes: tuple[str, ...],
    expected_code: str,
) -> None:
    client = RiotClient("test-key")

    def fail_safely(*_args, **_kwargs):
        raise RiotAPIError(
            "safe failure",
            exception_classes=exception_classes,
        )

    monkeypatch.setattr(client._client, "get_json", fail_safely)
    try:
        with pytest.raises(RiotApiError) as exc_info:
            client.account_by_riot_id("Player", "KR1")
    finally:
        client.close()

    assert exc_info.value.code == expected_code
    assert exc_info.value.exception_class == exception_classes[0]


def test_unknown_network_error_uses_safe_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RiotClient("test-key")

    def fail_safely(*_args, **_kwargs):
        raise RiotAPIError(
            "safe failure",
            exception_classes=("SomeNetworkError",),
        )

    monkeypatch.setattr(client._client, "get_json", fail_safely)
    try:
        with pytest.raises(RiotApiError) as exc_info:
            client.account_by_riot_id("Player", "KR1")
    finally:
        client.close()

    assert exc_info.value.code == "RIOT_NETWORK_ERROR"
    assert exc_info.value.stage == "account_v1"
    assert exc_info.value.http_status is None
    assert exc_info.value.exception_class == "SomeNetworkError"
