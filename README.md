# Pizza Feedback RAG Tutorial

This project implements a local retrieval-augmented generation pipeline for pizza feedback data, with evaluation tooling built around Ollama and Ragas.

## Standard project layout

- `src/` — application modules and reusable package code
- `scripts/` — execution entrypoints for evaluation and generation workflows
- `tests/` — test fixtures and regression data
- `data/` — raw datasets, generated artifacts, and Chroma persistence
- `results/` — exported evaluation outputs and summaries
- `docs/` — project documentation

## Quick start

1. Install dependencies:
   `pip install -r requirements.txt`
2. Start Ollama and ensure the required models are available.
3. Run the application entrypoints from `scripts/` or the compatibility wrappers at the project root.

## Notes

The project keeps canonical code under `src/` and uses the root-level files as compatibility shims so existing scripts continue to work while the repository follows a conventional Python directory layout.
