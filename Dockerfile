FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/fetchcore

RUN groupadd --gid 10001 fetchcore \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin fetchcore

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install ".[mcp]" \
    && mkdir -p /workspace \
    && chown fetchcore:fetchcore /workspace

USER fetchcore
WORKDIR /workspace

ENV FETCHCORE_MCP_WORKSPACE=/workspace

ENTRYPOINT ["fetchcore-mcp"]
