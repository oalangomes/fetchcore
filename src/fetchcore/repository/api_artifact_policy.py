"""P0 admission guard for API artifacts awaiting safe typed extraction.

This is deliberately a conservative quarantine, not a generic secrets detector.
No raw fallback for recognized OpenAPI, Swagger or Postman files. The guard
runs before canonical chunking and is also enforced at raw source serving.
S2/S3 may replace the quarantine only with tested sanitized extraction.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_API_EXTENSIONS = frozenset({".json", ".yaml", ".yml"})
API_ARTIFACT_BLOCK_REASONS = frozenset(
    "api_artifact_blocked_" + kind
    for kind in ("postman_environment", "postman_collection", "postman_v3", "swagger", "openapi")
)
_API_NAMES = {
    "swagger": "swagger",
    "openapi": "openapi",
}

# The marker check is intentionally syntactic and works with malformed or
# minified inputs: a parse failure must not restore unsafe raw-text fallback.
_OPENAPI_KEY = re.compile(r"""(?im)(?:^|[{,])\s*(?:"openapi"|'openapi'|openapi)\s*:""")
_SWAGGER_KEY = re.compile(r"""(?im)(?:^|[{,])\s*(?:"swagger"|'swagger'|swagger)\s*:""")
_POSTMAN_COLLECTION_SCHEMA = re.compile(
    r"schema\.getpostman\.com/json/collection/", re.IGNORECASE
)
_POSTMAN_SCOPE = re.compile(r"""(?i)["']?_postman_variable_scope["']?\s*:""")
_POSTMAN_ID = re.compile(r"""(?i)["']?_postman_id["']?\s*:""")


def blocked_api_artifact_kind(relative: str, content: str | None = None) -> str | None:
    """Identify source kinds that must not enter unstructured indexing.

    Returns a bounded kind, never a value derived from secrets. This detection
    is intentionally broader than strict validation: recognized malformed
    specifications/collections must fail closed until a safe extractor claims them.
    """

    path = PurePosixPath(relative)
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    if suffix not in _API_EXTENSIONS:
        return None

    if name.endswith((".postman_environment.json", ".postman_globals.json")):
        return "postman_environment"
    if name.endswith(".postman_collection.json"):
        return "postman_collection"
    if name.endswith((".request.yaml", ".request.yml")):
        return "postman_v3"
    for stem, kind in _API_NAMES.items():
        if path.stem.casefold() == stem or name.startswith((stem + "-", stem + "_")):
            return kind

    if content is None:
        return None

    # Refuse Postman signatures before considering arbitrary JSON/YAML keys.
    # Slash normalization handles JSON escaping of URLs (\u002F is deliberately
    # not interpreted; content still needs a parser before typed admission).
    if suffix == ".json":
        lowered = content.replace("\\/", "/")
        if _POSTMAN_COLLECTION_SCHEMA.search(lowered) or _POSTMAN_ID.search(content):
            return "postman_collection"
        if _POSTMAN_SCOPE.search(content):
            return "postman_environment"

    if _OPENAPI_KEY.search(content):
        return "openapi"
    if _SWAGGER_KEY.search(content):
        return "swagger"

    return None
