"""Expose the pizza RAG package's shared retrieval and generation API.

Exports prompt, model, chain, and build_rag_chain from rag_chain, plus
vector_store, retriever, get_vector_store, and get_retriever from vector_store.
Importing this package constructs shared clients and opens the review index;
first-time indexing requires review data and reachable Ollama embeddings.
"""

from src.rag_chain import build_rag_chain, chain, model, prompt
from src.vector_store import get_retriever, get_vector_store, retriever, vector_store

__all__ = [
    "retriever",
    "vector_store",
    "get_retriever",
    "get_vector_store",
    "chain",
    "prompt",
    "model",
    "build_rag_chain",
]
