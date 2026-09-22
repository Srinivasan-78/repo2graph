# Stage 1: Build
FROM python:3.12-slim AS builder

WORKDIR /build

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy source and install
COPY . .
RUN pip install --no-cache-dir ".[mcp,rag]"

# Stage 2: Runtime
FROM python:3.12-slim

# Prevent Python from writing .pyc files which breaks on read-only rootfs
ENV PYTHONDONTWRITEBYTECODE=1
# Keep stdout/stderr unbuffered
ENV PYTHONUNBUFFERED=1
ENV PATH="/opt/venv/bin:$PATH"

# Create a non-root user matching the docs (10000:10000)
RUN groupadd -g 10000 repo2graph && \
    useradd -u 10000 -g 10000 -s /bin/bash repo2graph

# Copy the virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv

WORKDIR /repo
USER 10000:10000

CMD ["repo2graph", "--help"]
