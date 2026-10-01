"""Compatibility alias for :mod:`fetchcore.projection.semantics`."""

from importlib import import_module as _import_module
from sys import modules as _modules

_modules[__name__] = _import_module("fetchcore.projection.semantics")
