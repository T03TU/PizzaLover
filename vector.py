"""Preserve the original vector module import API.

Re-exports src.vector_store's public factories and shared embeddings,
vector_store, and retriever objects. Importing it initializes the default
store and may ingest review data through the underlying package.
"""

from src.vector_store import *
