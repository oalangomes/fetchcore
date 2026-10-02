"""Compatibility alias for :mod:`fetchcore.adapters.embedding.ollama.provider`."""

from importlib import import_module as _import_module
from sys import modules as _modules

_provider = _import_module("fetchcore.adapters.embedding.ollama.provider")
from fetchcore.application.composition import configured_ollama_provider as _configured
_provider.configured_ollama_provider = _configured  # type: ignore[attr-defined]
_modules[__name__] = _provider
