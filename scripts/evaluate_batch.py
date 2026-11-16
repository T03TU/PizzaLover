"""Run reference-free batch evaluation of the pizza RAG pipeline.

TEST_QUESTIONS holds eight fixed pizza-review queries. PROJECT_ROOT is added
to sys.path so the script can import project modules when run directly.
main retrieves evidence, generates answers, prints metrics, and saves the
configured batch CSV. Imported retrieval resources initialize the index.
"""

import sys
from pathlib import Path
from time import perf_counter

import numpy as np

# Add project root to sys.path so config and src can be imported
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from src.evaluation.evaluator import RAGEvaluator
from src.rag_chain import chain
from src.vector_store import retriever

TEST_QUESTIONS = [
    "Which pizza franchise has the best pepperoni?",
    "What do customers think about Georgies Pizza?",
    "Is the service fast at Dominoes?",
    "Which location has the highest rated restaurants?",
    "What is the most popular pizza style mentioned in reviews?",
    "Are there any complaints about slow service?",
    "What makes Italianos special according to reviewers?",
    "Which franchise offers the best value for money?",
]


def main():
    """Generate answers for TEST_QUESTIONS and export reference-free scores.

    Returns:
        None. Prints per-question scores, aggregate statistics, and timing, then
        writes config.EVALUATION_RESULTS_CSV, replacing any existing file.

    Notes:
        questions, answers, and contexts_list are parallel lists passed to
        RAGEvaluator.evaluate_batch. metric_cols excludes sample input fields;
        display_df truncates questions only for terminal output. Requires Ollama.
    """
    evaluator = RAGEvaluator()

    questions, answers, contexts_list = [], [], []

    print("=" * 60)
    print("  Pizza RAG Pipeline — Batch Evaluation")
    print("=" * 60)

    for i, question in enumerate(TEST_QUESTIONS, 1):
        print(f"\n[{i}/{len(TEST_QUESTIONS)}] {question}")

        # Retrieve + generate
        retrieved_docs = retriever.invoke(question)
        contexts = [doc.page_content for doc in retrieved_docs]
        start = perf_counter()
        answer = chain.invoke({"reviews": retrieved_docs, "question": question})
        gen_time = perf_counter() - start

        print(f"  Answer: {answer[:120]}...")
        print(f"  Generation time: {np.round(gen_time, 2)}s")

        questions.append(question)
        answers.append(answer)
        contexts_list.append(contexts)

    # ── batch evaluation ──
    print("\n" + "=" * 60)
    print("  Running RAGAS evaluation on all responses...")
    print("=" * 60)

    eval_start = perf_counter()
    results_df = evaluator.evaluate_batch(questions, answers, contexts_list)
    eval_time = perf_counter() - eval_start

    # ── display results ──
    print("\n── Per-Question Scores ──")
    metric_cols = [
        c
        for c in results_df.columns
        if c not in ("user_input", "response", "retrieved_contexts", "reference")
    ]
    display_df = results_df[["user_input"] + metric_cols].copy()
    display_df["user_input"] = display_df["user_input"].str[:50]
    print(display_df.to_string(index=False))

    print(f"\n── Aggregate Scores ──")
    for col in metric_cols:
        vals = results_df[col].dropna()
        if not vals.empty:
            label = col.replace("_", " ").title()
            print(
                f"  {label:<35} mean={vals.mean():.4f}   std={vals.std():.4f}   min={vals.min():.4f}   max={vals.max():.4f}"
            )

    print(f"\nTotal evaluation time: {np.round(eval_time, 2)}s")

    # ── save to CSV ──
    output_path = config.EVALUATION_RESULTS_CSV
    results_df.to_csv(output_path, index=False)
    print(f"Detailed results saved to {output_path}")


if __name__ == "__main__":
    main()
