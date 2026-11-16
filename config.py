"""Define shared paths, model settings, and retrieval defaults for pizza RAG.

Attributes:
    PROJECT_ROOT: Directory containing this module; all project paths derive
        from it. DATA_DIR, RESULTS_DIR, DOCS_DIR, SRC_DIR, SCRIPTS_DIR,
        TESTS_DIR, and FIXTURES_DIR identify the corresponding project folders.
    RAW_DATA_PATH: Source feedback CSV in data/raw. TEST_DATASET_PATH points
        to its standardized fixture location; LEGACY_RAW_DATA_PATH provides
        the original input location used as a fallback by ingestion code.
    SYNTHETIC_DATA_DIR: Golden dataset folder. SYNTHETIC_GOLDEN_JSON and
        SYNTHETIC_GOLDEN_CSV are its structured and tabular output files.
    CHROMA_DB_DIR: Persistent review index directory. LEGACY_CHROMA_DB_DIR
        records the original index location for compatibility.
    CHAT_HISTORY_PATH: SQLite storage for local profiles and saved conversations.
    EVALUATION_HISTORY_PATH: JSON output for interactive evaluation history.
    EVALUATION_RESULTS_CSV: Batch evaluation report destination.
    GOLDEN_RESULTS_CSV: Reference-based evaluation report destination.
    WIKI_PATH: Project wiki documentation path.
    OLLAMA_BASE_URL: Server URL, overridden by the same-named environment
        variable; defaults to http://localhost:11434.
    LLM_MODEL: Generation/judge model, overridden by LLM_MODEL; defaults to
        llama3.2.
    EMBEDDING_MODEL: Embedding model, overridden by EMBEDDING_MODEL; defaults
        to mxbai-embed-large.
    RETRIEVER_K: Number of nearest reviews, parsed from RETRIEVER_K or 3.
    COLLECTION_NAME: Chroma collection name, pizza_feedback.
    TIKTOKEN_CACHE_DIR: Tokenizer cache path from the environment or
        ~/.cache/tiktoken; also written into os.environ.

Notes:
    Importing this module disables Ragas/Chroma telemetry and hosted LangSmith
    tracing, and creates data, raw/synthetic data, results, docs,
    tests, and fixture directories if missing. An invalid RETRIEVER_K value
    raises ValueError during import.
"""

import os
from pathlib import Path

# Keep review/question data and usage telemetry on this machine.
os.environ["RAGAS_DO_NOT_TRACK"] = "true"
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"

# ── Base Directories ──
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
RESULTS_DIR = PROJECT_ROOT / "results"
DOCS_DIR = PROJECT_ROOT / "docs"
SRC_DIR = PROJECT_ROOT / "src"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
TESTS_DIR = PROJECT_ROOT / "tests"
FIXTURES_DIR = TESTS_DIR / "fixtures"

# Ensure output and data directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "raw").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "synthetic").mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)
TESTS_DIR.mkdir(parents=True, exist_ok=True)
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

# ── Data File Paths ──
RAW_DATA_PATH = DATA_DIR / "raw" / "pizza_feedback_50_entries.csv"
SYNTHETIC_DATA_DIR = DATA_DIR / "synthetic"
SYNTHETIC_GOLDEN_JSON = SYNTHETIC_DATA_DIR / "synthetic_golden_dataset.json"
SYNTHETIC_GOLDEN_CSV = SYNTHETIC_DATA_DIR / "synthetic_golden_dataset.csv"
CHROMA_DB_DIR = DATA_DIR / "chroma_db"
CHAT_HISTORY_PATH = DATA_DIR / "chat_history.sqlite3"

# Standardized test fixtures and legacy compatibility paths
TEST_DATASET_PATH = FIXTURES_DIR / "pizza_feedback_50_entries.csv"
LEGACY_RAW_DATA_PATH = PROJECT_ROOT / "test-dataset" / "pizza_feedback_50_entries.csv"
LEGACY_CHROMA_DB_DIR = PROJECT_ROOT / "chrome_langchain_db"
# Reuse the legacy index when it exists and the canonical index is absent.
if not CHROMA_DB_DIR.exists() and LEGACY_CHROMA_DB_DIR.exists():
    CHROMA_DB_DIR = LEGACY_CHROMA_DB_DIR

# ── Results & Reports Paths ──
EVALUATION_HISTORY_PATH = RESULTS_DIR / "evaluation_history.json"
EVALUATION_RESULTS_CSV = RESULTS_DIR / "evaluation_results.csv"
GOLDEN_RESULTS_CSV = RESULTS_DIR / "golden_evaluation_results.csv"

# ── Documentation Paths ──
WIKI_PATH = DOCS_DIR / "WIKI.md"

# ── Ollama & LLM Settings ──
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.2")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "mxbai-embed-large")

# ── Retrieval Parameters ──
RETRIEVER_K = int(os.getenv("RETRIEVER_K", "3"))
COLLECTION_NAME = "pizza_feedback"

# ── Offline Cache Configuration ──
TIKTOKEN_CACHE_DIR = os.getenv("TIKTOKEN_CACHE_DIR", os.path.expanduser("~/.cache/tiktoken"))
os.environ["TIKTOKEN_CACHE_DIR"] = str(TIKTOKEN_CACHE_DIR)
