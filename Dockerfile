# Stage 1: Build stage with dependencies
FROM python:3.11-slim AS builder

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy ONLY locked requirements first for better layer caching
# This layer only rebuilds when dependencies change, not when code changes
# requirements.lock is pre-generated - no dependency resolution needed at build time
COPY requirements.lock ./

# Install dependencies into a virtual environment for cleaner copying
# Using pre-resolved requirements skips dependency resolution (saves ~30s)
RUN uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv pip install --no-cache -r requirements.lock

# Stage 2: Runtime stage - minimal image with pre-installed deps
FROM python:3.11-slim AS runtime

# Install gcloud CLI and AWS CLI in a separate layer (this rarely changes)
# Using smaller installation with just core components
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gnupg ca-certificates unzip && \
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee /etc/apt/sources.list.d/google-cloud-sdk.list && \
    curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg && \
    apt-get update && \
    apt-get install -y --no-install-recommends google-cloud-cli && \
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip" && \
    unzip -q /tmp/awscliv2.zip -d /tmp && \
    /tmp/aws/install && \
    rm -rf /tmp/aws /tmp/awscliv2.zip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Copy uv for potential runtime use
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy pre-built virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Activate virtual environment
ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV="/opt/venv"

WORKDIR /app

# Copy application code LAST (changes most frequently)
# This ensures code changes don't invalidate dependency cache
COPY server.py azure_tools.py aws_tools.py email_tools.py jira_tools.py ./
COPY app ./app

# Runtime configuration
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV UVICORN_TIMEOUT_KEEP_ALIVE=5

# Use exec form for proper signal handling
CMD ["python", "-u", "server.py"]
