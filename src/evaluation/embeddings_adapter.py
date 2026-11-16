"""Adapt Ragas embeddings to synchronous and asynchronous query/document APIs.

OllamaRagasEmbeddings delegates LangChain-style method names to inherited
Ragas OpenAIEmbeddings methods, using the caller's configured Ollama client.
"""

from ragas.embeddings import OpenAIEmbeddings


class OllamaRagasEmbeddings(OpenAIEmbeddings):
    """Expose LangChain-style embedding methods on Ragas OpenAIEmbeddings.

    Inherits client/model initialization from OpenAIEmbeddings. Callers configure
    an OpenAI-compatible Ollama client and an embedding model. Query methods
    embed one string; document methods embed batches, preserving input order.
    The inherited methods handle API requests and propagate request failures.
    """

    def embed_query(self, text: str) -> list[float]:
        """Request embeddings for one query.

        Args:
            text: Query string to embed.

        Returns:
            The query embedding as a list of floating-point coordinates.
        """
        return self.embed_text(text)

    async def aembed_query(self, text: str) -> list[float]:
        """Asynchronously request embeddings for one query.

        Args:
            text: Query string to embed.

        Returns:
            The query embedding as a list of floating-point coordinates.
        """
        return await self.aembed_text(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Request embeddings for a batch of documents.

        Args:
            texts: Document strings to embed in input order.

        Returns:
            A list of numeric vectors in the same order as texts.
        """
        return self.embed_texts(texts)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """Asynchronously request embeddings for a batch of documents.

        Args:
            texts: Document strings to embed in input order.

        Returns:
            A list of numeric vectors in the same order as texts.
        """
        return await self.aembed_texts(texts)
