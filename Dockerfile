FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
# Copy project files first for proper uv install
COPY pyproject.toml server.py ./
# Install dependencies from pyproject.toml (uv pip install . reads from pyproject.toml)
RUN uv pip install --system --no-cache .
ENV PORT=8080
CMD ["python", "server.py"]
