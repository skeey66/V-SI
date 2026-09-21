from pathlib import Path

import yaml

from tests.integration.harness import REPO_ROOT


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())


def test_workspace_service_exists() -> None:
    assert "workspace" in _compose()["services"]


def test_workspace_has_no_internet_route() -> None:
    """생성 코드가 외부로 나갈 경로가 네트워크 수준에 없어야 한다 (스펙 §4.2)."""
    compose = _compose()
    nets = compose["services"]["workspace"]["networks"]
    assert list(nets) == ["internal"]
    assert compose["networks"]["internal"]["internal"] is True


def test_workspace_mounts_no_host_path() -> None:
    assert "volumes" not in _compose()["services"]["workspace"]


def test_agents_reach_both_networks() -> None:
    """에이전트는 MCP(internal)와 Ollama(기본)를 모두 써야 한다."""
    services = _compose()["services"]
    for agent in ("planner", "dev", "qa", "security"):
        nets = set(services[agent].get("networks") or [])
        assert {"internal", "default"} <= nets, agent


def test_orchestrator_is_not_on_internal_network() -> None:
    """오케스트레이터는 워크스페이스에 닿을 이유가 없다."""
    nets = _compose()["services"]["orchestrator"].get("networks") or ["default"]
    assert "internal" not in nets
