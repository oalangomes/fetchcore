# Optional Search Lab/MCP development deployment. Never part of core packaging.
FROM python:3.11-slim AS python-runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY tools/explorer/api/ ./tools/explorer/api/
COPY tools/explorer/docker/mcp_http.py ./tools/explorer/docker/mcp_http.py
COPY tools/explorer/docker/smoke.py ./tools/explorer/docker/smoke.py
RUN python -m pip install --no-cache-dir ".[mcp]" -r tools/explorer/api/requirements.txt

FROM node:22-alpine AS web-build
WORKDIR /web
COPY tools/explorer/web/package.json ./
RUN npm install --no-audit --no-fund
COPY tools/explorer/web/ ./
RUN npm run build

FROM nginx:1.27-alpine AS web-runtime
COPY tools/explorer/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=web-build /web/dist/ /usr/share/nginx/html/
EXPOSE 8080
