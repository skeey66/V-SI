FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY packages ./packages
COPY services ./services
COPY scenarios ./scenarios
RUN pip install --no-cache-dir -e .
ENV PYTHONPATH=/app/packages:/app/services
CMD ["uvicorn", "stub_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
