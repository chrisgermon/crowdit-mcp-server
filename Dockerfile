FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
# Copy project files
COPY pyproject.toml server.py ./
# Install dependencies only (not the package itself)
RUN uv pip install --system --no-cache \
    "fastmcp>=2.0.0" \
    "httpx>=0.27.0" \
    "pydantic>=2.0.0" \
    "uvicorn>=0.30.0" \
    "starlette>=0.38.0" \
    "google-cloud-secret-manager>=2.20.0" \
    "google-cloud-bigquery>=3.25.0" \
    "google-auth>=2.29.0" \
    "asyncssh>=2.14.0" \
    "pymysql>=1.1.0" \
    "google-cloud-build>=3.20.0"
ENV PORT=8080
CMD ["python", "server.py"]
