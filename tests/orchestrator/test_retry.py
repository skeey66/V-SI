import httpx
import pytest
from orchestrator.retry import FailureClass, backoff_seconds, classify, max_attempts


def test_connect_error_is_transport():
    assert classify(httpx.ConnectError("refused")) is FailureClass.TRANSPORT


def test_http_500_is_transport():
    resp = httpx.Response(500, request=httpx.Request("POST", "http://x"))
    assert classify(httpx.HTTPStatusError("boom", request=resp.request, response=resp)) is FailureClass.TRANSPORT


def test_http_422_is_poison():
    resp = httpx.Response(422, request=httpx.Request("POST", "http://x"))
    assert classify(httpx.HTTPStatusError("bad", request=resp.request, response=resp)) is FailureClass.POISON


def test_value_error_is_poison():
    assert classify(ValueError("schema")) is FailureClass.POISON


def test_runtime_error_is_execution():
    assert classify(RuntimeError("executor blew up")) is FailureClass.EXECUTION


def test_timeout_error_is_transport():
    """asyncio.wait_for가 던지는 내장 TimeoutError도 전송 실패다.

    엔진이 개별 submit 시도를 `self._timeouts.tool_s`로 감싸므로(Task 12),
    거기서 나는 타임아웃도 httpx의 타임아웃과 같은 층으로 분류돼야 한다.
    """
    assert classify(TimeoutError("tool timeout")) is FailureClass.TRANSPORT


def test_poison_is_never_retried():
    assert max_attempts(FailureClass.POISON) == 1
    assert max_attempts(FailureClass.TRANSPORT) == 3
    assert max_attempts(FailureClass.EXECUTION) == 2


def test_backoff_grows():
    assert backoff_seconds(1) < backoff_seconds(2) < backoff_seconds(3)
