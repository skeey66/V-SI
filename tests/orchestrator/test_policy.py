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
