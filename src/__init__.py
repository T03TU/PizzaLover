"""Pizza RAG public API with lazy resources to avoid import-time indexing."""

from importlib import import_module

import config  # Apply local-only telemetry and cache settings before engine imports.

__all__ = ["retriever", "vector_store", "get_retriever", "get_vector_store",
           "chain", "prompt", "model", "build_rag_chain"]


def __getattr__(name):
    """Resolve legacy exports only when requested, without eager index creation."""
    if name not in __all__:
        raise AttributeError(name)
    module = "vector_store" if name in __all__[:4] else "rag_chain"
    return getattr(import_module(f"src.{module}"), name)
