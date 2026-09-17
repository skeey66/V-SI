import httpx
import pytest
from orchestrator.retry import FailureClass, backoff_seconds, classify, is_undelivered, max_attempts


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


# ------------------------------------------------- 전달 여부가 증명되는 실패만 그 자리에서 재시도


def test_connect_error_is_provably_undelivered():
    """연결 자체가 거부됐다 — 상대가 요청을 받았을 가능성이 없다."""
    assert is_undelivered(httpx.ConnectError("refused")) is True


def test_connect_timeout_is_provably_undelivered():
    """TCP 연결 수립 자체가 타임아웃났다 — 바이트 하나도 못 나갔다."""
    assert is_undelivered(httpx.ConnectTimeout("connect timeout")) is True


def test_read_timeout_is_ambiguous():
    """연결은 됐고 응답을 기다리다 타임아웃났다 — 상대가 이미 받았을 수 있다."""
    assert is_undelivered(httpx.ReadTimeout("read timeout")) is False


def test_http_500_is_ambiguous():
    """상대가 요청을 받고 나서 5xx를 낸 것일 수 있다 — 그 자리에서 다시 보내면 중복 위험."""
    resp = httpx.Response(500, request=httpx.Request("POST", "http://x"))
    exc = httpx.HTTPStatusError("boom", request=resp.request, response=resp)
    assert is_undelivered(exc) is False


def test_generic_wait_for_timeout_is_ambiguous():
    """`asyncio.wait_for`가 전체 submit()을 감싸므로, 이 타임아웃은 에이전트가
    이미 작업을 받아 처리 중이었을 가능성을 배제하지 못한다."""
    assert is_undelivered(TimeoutError("tool timeout")) is False


def test_value_error_is_ambiguous():
    assert is_undelivered(ValueError("schema")) is False
