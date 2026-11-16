"""Index pizza review CSV rows and expose Chroma similarity retrieval.

The factory functions configure Ollama embeddings, persistent storage, and
retrieval. embeddings, vector_store, and retriever are shared instances
created at import time; an absent or empty database directory triggers CSV
loading, remote embedding requests, and persistent index writes.
"""

import os
from pathlib import Path
from typing import Optional

import pandas as pd
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStoreRetriever
from langchain_ollama import OllamaEmbeddings

import config


def get_embeddings(
    model_name: str = config.EMBEDDING_MODEL,
    base_url: str = config.OLLAMA_BASE_URL,
) -> OllamaEmbeddings:
    """Create the Ollama embedding client used to index and search reviews.

    Args:
        model_name: Ollama embedding model identifier.
        base_url: Base URL of the Ollama server.

    Returns:
        An OllamaEmbeddings client; embedding requests occur when it is used.
    """
    return OllamaEmbeddings(model=model_name, base_url=base_url)


def init_vector_store(
    db_path: Path = config.CHROMA_DB_DIR,
    csv_path: Path = config.RAW_DATA_PATH,
    collection_name: str = config.COLLECTION_NAME,
    embeddings: Optional[OllamaEmbeddings] = None,
) -> Chroma:
    """Open a persistent Chroma store and ingest reviews if its directory is empty.

    Args:
        db_path: Directory holding the persistent Chroma database.
        csv_path: Preferred feedback CSV; the legacy path is tried if absent.
        collection_name: Chroma collection to create or open.
        embeddings: Embedding client, or None to create the configured client.

    Returns:
        The persistent Chroma collection wrapped as a vector store.

    Raises:
        FileNotFoundError: Initial ingestion requires a CSV and neither path exists.
        KeyError: The CSV lacks a required feedback column.

    Notes:
        needs_ingestion checks directory existence and contents, not collection
        size. resolved_csv selects the input file. Each row's Comment becomes
        document text; Feedback_ID, Franchise, Location, and Rating become
        metadata. ids uses review_<row index> identifiers. Ingestion writes to
        disk and requests embeddings from Ollama.
    """
    if embeddings is None:
        embeddings = get_embeddings()

    # Determine if documents need to be ingested
    needs_ingestion = not os.path.exists(db_path) or len(os.listdir(db_path)) == 0

    vector_store = Chroma(
        collection_name=collection_name,
        persist_directory=str(db_path),
        embedding_function=embeddings,
    )

    if needs_ingestion:
        # Resolve CSV path (check standard data/raw, then fallback to legacy location)
        resolved_csv = csv_path if csv_path.exists() else config.LEGACY_RAW_DATA_PATH
        if not resolved_csv.exists():
            raise FileNotFoundError(f"Customer feedback dataset not found at {csv_path} or {config.LEGACY_RAW_DATA_PATH}")

        print(f"Indexing customer feedback records from {resolved_csv} into {db_path}...")
        df = pd.read_csv(resolved_csv)
        documents = []
        ids = []

        for i, row in df.iterrows():
            doc = Document(
                page_content=row["Comment"],
                metadata={
                    "Feedback_ID": row["Feedback_ID"],
                    "Franchise": row["Franchise"],
                    "Location": row["Location"],
                    "Rating": row["Rating"],
                },
            )
            documents.append(doc)
            ids.append(f"review_{i}")

        vector_store.add_documents(documents, ids=ids)
        print(f"Successfully indexed {len(documents)} review documents into ChromaDB collection '{collection_name}'.")

    return vector_store


def get_vector_store(
    db_path: Path = config.CHROMA_DB_DIR,
    csv_path: Path = config.RAW_DATA_PATH,
    collection_name: str = config.COLLECTION_NAME,
    embeddings: Optional[OllamaEmbeddings] = None,
) -> Chroma:
    """Open or initialize the store through the compatibility API.

    Args:
        db_path: Directory holding the persistent Chroma database.
        csv_path: Preferred feedback CSV; the legacy path is tried if absent.
        collection_name: Chroma collection to create or open.
        embeddings: Embedding client, or None to create the configured client.

    Returns:
        The persistent Chroma collection wrapped as a vector store.

    Raises:
        FileNotFoundError: Initial ingestion requires a CSV and neither path exists.
        KeyError: The CSV lacks a required feedback column.

    Notes:
        Delegates to init_vector_store, including its ingestion side effects.
    """
    return init_vector_store(
        db_path=db_path,
        csv_path=csv_path,
        collection_name=collection_name,
        embeddings=embeddings,
    )


def get_retriever(
    k: int = config.RETRIEVER_K,
    vector_store: Optional[Chroma] = None,
) -> VectorStoreRetriever:
    """Create a similarity retriever over the supplied or configured store.

    Args:
        k: Maximum number of nearest review documents to retrieve per query.
        vector_store: Existing Chroma store, or None to initialize the default.

    Returns:
        A VectorStoreRetriever whose invoke method accepts a query string.

    Notes:
        Initializing the default store can ingest the CSV and persist embeddings.
    """
    if vector_store is None:
        vector_store = init_vector_store()
    return vector_store.as_retriever(search_kwargs={"k": k})


# Module-level instances for direct import compatibility
embeddings = get_embeddings()
vector_store = init_vector_store(embeddings=embeddings)
retriever = get_retriever(vector_store=vector_store)

__all__ = [
    "get_embeddings",
    "init_vector_store",
    "get_vector_store",
    "get_retriever",
    "embeddings",
    "vector_store",
    "retriever",
]
