FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
COPY services/buyer-audit-api/pyproject.toml services/buyer-audit-api/uv.lock ./
RUN uv sync --frozen --no-dev
COPY services/buyer-audit-api/src ./src

ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "buyer_audit_api.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
