"""Expose evaluation, embedding adaptation, and golden dataset helpers.

RAGEvaluator scores responses; OllamaRagasEmbeddings adapts embedding calls.
PERSONAS and SEED_SYNTHETIC_TESTSET describe generation roles and fallback
cases. load_or_generate_golden_dataset and save_golden_dataset manage caches.
Importing this subpackage also runs the parent src package initialization.
"""

from src.evaluation.embeddings_adapter import OllamaRagasEmbeddings
from src.evaluation.evaluator import RAGEvaluator
from src.evaluation.golden_generator import (
    PERSONAS,
    SEED_SYNTHETIC_TESTSET,
    load_or_generate_golden_dataset,
    save_golden_dataset,
)

__all__ = [
    "RAGEvaluator",
    "OllamaRagasEmbeddings",
    "load_or_generate_golden_dataset",
    "save_golden_dataset",
    "PERSONAS",
    "SEED_SYNTHETIC_TESTSET",
]
