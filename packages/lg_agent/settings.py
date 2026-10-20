"""환경 설정.

`.env` 의 키 이름은 표준(`OPENAI_API_KEY`, `LANGSMITH_API_KEY`)과 사용자가 쓴
이름(`OPENAPI_APIKEY`, `LANGSMITH_APIKEY`) 둘 다 받는다. 읽은 값은 표준 이름으로
`os.environ` 에 다시 넣는다 — `langchain-openai` 와 `langsmith` 는 표준 이름만
읽기 때문이다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_ALIASES: dict[str, tuple[str, ...]] = {
    "OPENAI_API_KEY": ("OPENAI_API_KEY", "OPENAPI_APIKEY", "OPENAI_APIKEY"),
    "LANGSMITH_API_KEY": ("LANGSMITH_API_KEY", "LANGSMITH_APIKEY"),
}


def _first(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


@dataclass(frozen=True)
class Settings:
    model: str
    mcp_base_url: str
    max_revisions: int
    agent_budget_s: float
    max_turns: int
    max_output_tokens: int
    checkpoint_url: str | None
    langsmith_project: str
    tracing: bool


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or Path.cwd() / ".env", override=False)
    for standard, names in _ALIASES.items():
        value = _first(names)
        if value:
            os.environ[standard] = value

    project = os.environ.get("LANGSMITH_PROJECT", "v-si")
    tracing = bool(os.environ.get("LANGSMITH_API_KEY"))
    if tracing:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_PROJECT", project)

    return Settings(
        model=os.environ.get("VSI_LG_MODEL", "gpt-4o-mini"),
        mcp_base_url=os.environ.get("VSI_MCP_BASE_URL", "http://localhost:8010").rstrip("/"),
        max_revisions=int(os.environ.get("VSI_LG_MAX_REVISIONS", "3")),
        agent_budget_s=float(os.environ.get("VSI_LG_AGENT_BUDGET_S", "300")),
        # 비용 상한 두 개. 실측 기준 3회차 전체가 약 4만 토큰(0.0064달러)이었다.
        max_turns=int(os.environ.get("VSI_LG_MAX_TURNS", "8")),
        max_output_tokens=int(os.environ.get("VSI_LG_MAX_OUTPUT_TOKENS", "2000")),
        checkpoint_url=os.environ.get("VSI_LG_CHECKPOINT_URL") or None,
        langsmith_project=project,
        tracing=tracing,
    )
