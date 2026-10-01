"""Compatibility alias for :mod:`fetchcore.backends.solr_incremental_persistence`."""

from importlib import import_module as _import_module
from sys import modules as _modules

_modules[__name__] = _import_module("fetchcore.backends.solr_incremental_persistence")
