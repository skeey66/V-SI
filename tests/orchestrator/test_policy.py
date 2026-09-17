import pytest
from orchestrator.policy import TimeoutConfig, TimeoutConfigError


def test_valid_config_passes():
    TimeoutConfig(tool_s=30, executor_s=300, step_s=900, run_s=3600).validate()


def test_executor_not_greater_than_tool_raises():
    cfg = TimeoutConfig(tool_s=300, executor_s=300, step_s=900, run_s=3600)
    with pytest.raises(TimeoutConfigError, match="executor_s"):
        cfg.validate()


def test_step_smaller_than_executor_raises():
    cfg = TimeoutConfig(tool_s=30, executor_s=900, step_s=300, run_s=3600)
    with pytest.raises(TimeoutConfigError, match="step_s"):
        cfg.validate()


def test_from_env_uses_defaults():
    cfg = TimeoutConfig.from_env({})
    assert (cfg.tool_s, cfg.executor_s, cfg.step_s, cfg.run_s) == (30, 300, 900, 3600)


def test_from_env_reads_overrides():
    cfg = TimeoutConfig.from_env(
        {
            "VSI_TIMEOUT_TOOL_S": "5",
            "VSI_TIMEOUT_EXECUTOR_S": "10",
            "VSI_TIMEOUT_STEP_S": "20",
            "VSI_TIMEOUT_RUN_S": "40",
        }
    )
    assert (cfg.tool_s, cfg.executor_s, cfg.step_s, cfg.run_s) == (5, 10, 20, 40)


def test_non_numeric_env_raises_timeout_config_error():
    """오타 난 env 값은 `TimeoutConfigError`로 나와야 한다.

    `from_env`는 `orchestrator/main.py`의 **import 시점**에 돈다 — 여기서 맨
    `ValueError`가 나가면 컨테이너가 "설정이 틀렸다"가 아니라 정체 불명의
    예외로 죽는다. 잘못된 부등식(`validate`)과 잘못된 값(`int()`)은 원인이
    같으므로(설정 오류) 같은 타입으로 보고한다. 어느 변수가 문제인지도
    메시지에 들어가야 한다 — 네 개 중 하나를 찾아 헤매지 않도록.
    """
    with pytest.raises(TimeoutConfigError, match="VSI_TIMEOUT_TOOL_S"):
        TimeoutConfig.from_env({"VSI_TIMEOUT_TOOL_S": "삼십초"})
