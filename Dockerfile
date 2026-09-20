FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY voice_gate ./voice_gate
RUN uv sync --frozen --no-dev --no-cache && useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 VG_DATABASE=/data/captures.sqlite
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3)"
CMD ["uvicorn", "voice_gate.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
