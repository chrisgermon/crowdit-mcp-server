FROM python:3.12-slim
# Install gcloud CLI
RUN apt-get update && apt-get install -y curl gnupg && \
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list && \
    curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg && \
    apt-get update && apt-get install -y google-cloud-cli && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
# Copy project files
COPY pyproject.toml server.py azure_tools.py ./
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
    "google-cloud-build>=3.20.0" \
    "azure-identity>=1.15.0" \
    "azure-mgmt-resource>=23.0.0" \
    "azure-mgmt-network>=25.0.0" \
    "azure-mgmt-compute>=30.0.0" \
    "azure-mgmt-storage>=21.0.0" \
    "azure-mgmt-subscription>=3.1.0" \
    "azure-mgmt-monitor>=6.0.0"
ENV PORT=8080
# Set startup timeout for uvicorn
ENV UVICORN_TIMEOUT_KEEP_ALIVE=5
CMD ["python", "-u", "server.py"]