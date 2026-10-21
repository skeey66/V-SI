FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY backend ./backend
COPY mcp_servers ./mcp_servers
COPY legacy/packages ./legacy/packages
COPY legacy/services ./legacy/services
COPY legacy/scenarios ./legacy/scenarios
RUN pip install --no-cache-dir -e .
# backend·mcp_servers = LangGraph 버전, legacy/* = 이전 버전(A2A). 같은 이미지를 쓴다.
ENV PYTHONPATH=/app/backend:/app/mcp_servers:/app/legacy/packages:/app/legacy/services:/app/legacy
CMD ["uvicorn", "agent_entry.main:app", "--host", "0.0.0.0", "--port", "8000"]
