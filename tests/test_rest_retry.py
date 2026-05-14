"""Retry/error handling tests for the live REST wrapper."""

from __future__ import annotations

from typing import Any

import pytest
from massive.exceptions import AuthError, BadResponse

from agora.config import MassiveConfig
from agora.errors import MassiveAPIError, MassiveAuthenticationError, MassiveRateLimitError
from agora.loaders.rest import MassiveDataApi


class FakeSdkClient:
    """Minimal stand-in for Massive's RESTClient."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls = 0

    def get_aggs(self, **kwargs):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def bad_response(status_code: int) -> BadResponse:
    exc = BadResponse(f"HTTP {status_code}")
    exc.status_code = status_code
    return exc


def api_with_sdk(sdk: FakeSdkClient, *, max_retries: int) -> MassiveDataApi:
    api = MassiveDataApi.__new__(MassiveDataApi)
    api.config = MassiveConfig(api_key="test-key", max_retries=max_retries)
    api._client = sdk
    return api


def get_aggregates(api: MassiveDataApi):
    return api.get_aggregates(
        "AAPL",
        1,
        "day",
        "2024-01-01",
        "2024-01-31",
    )


def test_get_aggregates_retries_rate_limit_then_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("agora.loaders.rest.time.sleep", sleep_calls.append)
    sdk = FakeSdkClient([bad_response(429), ["bar"]])
    api = api_with_sdk(sdk, max_retries=1)

    assert get_aggregates(api) == ["bar"]

    assert sdk.calls == 2
    assert sleep_calls == [1.0]


def test_get_aggregates_rate_limit_exhaustion_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("agora.loaders.rest.time.sleep", sleep_calls.append)
    sdk = FakeSdkClient([bad_response(429), bad_response(429)])
    api = api_with_sdk(sdk, max_retries=1)

    with pytest.raises(MassiveRateLimitError, match="Rate limit exceeded"):
        get_aggregates(api)

    assert sdk.calls == 2
    assert sleep_calls == [1.0]


def test_get_aggregates_auth_error_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("agora.loaders.rest.time.sleep", sleep_calls.append)
    sdk = FakeSdkClient([AuthError("invalid key"), ["bar"]])
    api = api_with_sdk(sdk, max_retries=2)

    with pytest.raises(MassiveAuthenticationError, match="Authentication failed"):
        get_aggregates(api)

    assert sdk.calls == 1
    assert sleep_calls == []


@pytest.mark.parametrize("status_code", [401, 403])
def test_get_aggregates_http_auth_error_does_not_retry(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("agora.loaders.rest.time.sleep", sleep_calls.append)
    sdk = FakeSdkClient([bad_response(status_code), ["bar"]])
    api = api_with_sdk(sdk, max_retries=2)

    with pytest.raises(MassiveAuthenticationError, match="Authentication failed"):
        get_aggregates(api)

    assert sdk.calls == 1
    assert sleep_calls == []


def test_get_aggregates_other_bad_response_retries_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("agora.loaders.rest.time.sleep", sleep_calls.append)
    sdk = FakeSdkClient([bad_response(500), bad_response(500)])
    api = api_with_sdk(sdk, max_retries=1)

    with pytest.raises(MassiveAPIError, match="API request failed after 1 retries"):
        get_aggregates(api)

    assert sdk.calls == 2
    assert sleep_calls == [1.0]


def test_get_aggregates_unexpected_error_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("agora.loaders.rest.time.sleep", sleep_calls.append)
    sdk = FakeSdkClient([RuntimeError("boom"), ["bar"]])
    api = api_with_sdk(sdk, max_retries=2)

    with pytest.raises(RuntimeError, match="boom"):
        get_aggregates(api)

    assert sdk.calls == 1
    assert sleep_calls == []
