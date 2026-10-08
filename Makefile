PYTHON ?= python3
IMAGE ?= fetchcore:dev
DOCKER ?= docker

.PHONY: help install-dev lint typecheck test build check docker-build docker-smoke compose-config runtime-check docs-check

help:
	@printf '%s\n' \
		'FetchCore developer commands:' \
		'  make install-dev    Install editable development dependencies' \
		'  make lint           Run Ruff' \
		'  make typecheck      Run mypy' \
		'  make test           Run pytest with coverage collection' \
		'  make build          Build Python sdist/wheel' \
		'  make docs-check     Validate documentation links and index' \
		'  make check          Run lint + typecheck + test + build + docs checks' \
		'  make docker-build   Build the FetchCore runtime image' \
		'  make docker-smoke   Verify non-root image, package import and MCP entry point' \
		'  make compose-config Validate Docker Compose configuration' \
		'  make runtime-check  Validate Compose and container runtime contracts'

install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src

test:
	$(PYTHON) -m coverage run -m pytest -q
	$(PYTHON) -m coverage report --fail-under=0

build:
	$(PYTHON) -m build

docs-check:
	$(PYTHON) scripts/check_docs.py
	$(PYTHON) scripts/docs/localize.py check

check: lint typecheck test build docs-check

docker-build:
	$(DOCKER) build --tag $(IMAGE) .

docker-smoke: docker-build
	$(DOCKER) run --rm --entrypoint sh $(IMAGE) -c 'test "$$(id -u)" -ne 0 && command -v fetchcore-mcp >/dev/null && python -c "import fetchcore; import importlib.metadata; assert importlib.metadata.version(\"fetchcore\")"'

compose-config:
	$(DOCKER) compose --profile runtime --profile solr --profile opensearch --profile qdrant config --quiet

runtime-check: compose-config docker-smoke
