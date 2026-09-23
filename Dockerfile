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

# git, because repo2graph shells out to it rather than reimplementing it:
# `walker.discover` prefers `git ls-files` and only falls back to `os.walk`,
# which does not honour .gitignore, so without git the image indexes a
# different set of files than every other way of running the same build.
# `--git-history` (CO_CHANGE) and `repo2graph github` need it outright, and
# `doctor`'s own remediation for this image would read "Install Git so
# repo2graph can discover files with git ls-files and compute CO_CHANGE edges".
# --no-install-recommends keeps this to git and its libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user matching the docs (10000:10000)
RUN groupadd -g 10000 repo2graph && \
    useradd -u 10000 -g 10000 -s /bin/bash repo2graph

# Copy the virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv

WORKDIR /repo
USER 10000:10000

# The documented run mounts a host checkout at /repo and runs as 10000:10000,
# so /repo is owned by whoever owns it on the host and git refuses it with
# "detected dubious ownership". Declared through GIT_CONFIG_* rather than
# `git config --global`, because the documented run is also --read-only: there
# is no writable HOME for a config file to land in. Scoped to this image, whose
# only job is to read the one repository mounted into it.
ENV GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0=safe.directory \
    GIT_CONFIG_VALUE_0=*

CMD ["repo2graph", "--help"]
