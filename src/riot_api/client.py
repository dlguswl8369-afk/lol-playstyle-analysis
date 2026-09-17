from __future__ import annotations

import time
from typing import Any

import httpx


class RiotAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retries: int = 0,
        exception_classes: tuple[str, ...] = (),
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retries = retries
        self.exception_classes = exception_classes
        self.exception_class = exception_classes[0] if exception_classes else None
        self.inner_exception_class = exception_classes[1] if len(exception_classes) > 1 else None


def _exception_classes(exc: BaseException) -> tuple[str, ...]:
    classes: list[str] = []
    current: BaseException | None = exc
    while current is not None and len(classes) < 8:
        classes.append(current.__class__.__name__)
        current = current.__cause__ or current.__context__
    return tuple(classes)


class RiotAPIClient:
    """Riot API 공통 클라이언트. API 키는 예외나 로그에 포함하지 않는다."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 15.0,
        request_interval: float = 0.15,
        max_retries: int = 3,
        trust_env: bool = True,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("RIOT_API_KEY가 설정되지 않았습니다.")
        self.request_interval = request_interval
        self.max_retries = max_retries
        self._last_request_at = 0.0
        self._client = httpx.Client(
            headers={"X-Riot-Token": api_key},
            timeout=timeout,
            trust_env=trust_env,
            transport=transport,
        )

    def __enter__(self) -> RiotAPIClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        retries = 0
        while True:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.request_interval:
                time.sleep(self.request_interval - elapsed)
            try:
                response = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                if retries >= self.max_retries:
                    exception_classes = _exception_classes(exc)
                    raise RiotAPIError(
                        "Riot API에 연결하지 못했습니다.",
                        retries=retries,
                        exception_classes=exception_classes,
                    ) from exc
                time.sleep(2**retries)
                retries += 1
                continue
            finally:
                self._last_request_at = time.monotonic()

            if response.status_code == 200:
                return response.json()
            if response.status_code == 429 and retries < self.max_retries:
                try:
                    delay = max(0.0, float(response.headers.get("Retry-After", "1")))
                except ValueError:
                    delay = 1.0
                time.sleep(delay)
                retries += 1
                continue
            if response.status_code in {500, 502, 503, 504} and retries < self.max_retries:
                time.sleep(2**retries)
                retries += 1
                continue

            messages = {
                401: "Riot API Key가 올바르지 않습니다.",
                403: "Riot API Key가 만료되었거나 이 API를 호출할 권한이 없습니다.",
                404: "요청한 Riot ID 또는 경기 데이터를 찾을 수 없습니다.",
                429: "Riot API 호출 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.",
            }
            message = messages.get(
                response.status_code,
                f"Riot API 요청에 실패했습니다(HTTP {response.status_code}).",
            )
            raise RiotAPIError(message, response.status_code, retries)
