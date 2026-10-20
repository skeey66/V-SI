"""V-SI 협업 흐름의 LangGraph 구현.

기존 경로(`orchestrator` + A2A + `llm_agent`)는 지우지 않고 Before 비교군으로
남긴다. 이 패키지는 같은 MCP 도구 서버(`services/tool_server`)를 그대로 쓰고,
협업 흐름(기획 → 개발 → QA·보안 → 판정 → 환류)만 `StateGraph` 로 옮긴다.
"""
