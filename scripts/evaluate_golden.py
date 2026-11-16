"""Evaluate pizza RAG answers against cached or synthesized golden references.

PROJECT_ROOT is added to sys.path for direct script execution. The workflow
runs six Ragas metrics, prints per-case and aggregate scores, and writes the
configured golden results CSV. CLI options --regenerate and --limit control
case creation and selection. Imported retrieval resources open the index.
"""

import argparse
import sys
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
from instructor import Mode
from openai import AsyncOpenAI
from ragas import EvaluationDataset, SingleTurnSample, evaluate
from ragas.llms import llm_factory
from ragas.run_config import RunConfig
from ragas.metrics._answer_correctness import AnswerCorrectness
from ragas.metrics._answer_relevance import AnswerRelevancy
from ragas.metrics._answer_similarity import AnswerSimilarity
from ragas.metrics._context_precision import ContextPrecision
from ragas.metrics._context_recall import ContextRecall
from ragas.metrics._faithfulness import Faithfulness

# Add project root to sys.path so config and src can be imported
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from src.evaluation.embeddings_adapter import OllamaRagasEmbeddings
from src.evaluation.golden_generator import load_or_generate_golden_dataset
from src.rag_chain import chain as rag_chain
from src.vector_store import retriever


def build_evaluator_metrics(llm, embeddings):
    """Configure the six metrics used to evaluate answers against references.

    Args:
        llm: Ragas-compatible judge used for textual evaluation.
        embeddings: Ragas-compatible embedding adapter for relevancy, correctness,
            and semantic similarity.

    Returns:
        Metrics in order: faithfulness, answer relevancy, context precision,
        context recall, answer correctness, and answer similarity. The local
        variables f, ar, cp, cr, ac, and asim hold these configured instances.
    """
    f = Faithfulness()
    f.llm = llm

    ar = AnswerRelevancy()
    ar.llm = llm
    ar.embeddings = embeddings

    cp = ContextPrecision()
    cp.llm = llm

    cr = ContextRecall()
    cr.llm = llm

    ac = AnswerCorrectness()
    ac.llm = llm
    ac.embeddings = embeddings

    asim = AnswerSimilarity()
    asim.embeddings = embeddings

    return [f, ar, cp, cr, ac, asim]


def run_golden_evaluation(force_regenerate: bool = False, limit: int = None):
    """Generate RAG answers for golden cases, score them, and export results.

    Args:
        force_regenerate: Request fresh Ollama synthesis instead of using cached
            cases; generation failures fall back to the curated seed dataset.
        limit: Evaluate only the first N cases when positive; None, zero, or a
            negative value evaluates all loaded cases.

    Returns:
        A pandas DataFrame containing sample fields and six metric score columns.

    Notes:
        samples pairs model answers and retrieved review text with golden
        references and reference contexts. generation_times stores inference
        durations in seconds. present_metrics selects score columns returned by
        Ragas. Prints detailed statistics and overwrites config.GOLDEN_RESULTS_CSV.
        Requires the configured Ollama generation and embedding models.
    """
    print("=" * 72)
    print("  Pizza RAG Pipeline — Automated Synthetic Golden Dataset Evaluation")
    print("=" * 72)

    golden_cases = load_or_generate_golden_dataset(force_regenerate=force_regenerate)
    if limit and limit > 0:
        golden_cases = golden_cases[:limit]
        print(f"Limiting evaluation to first {limit} test cases.")

    print(f"\nLoaded {len(golden_cases)} synthetic golden test cases.")

    print("\n" + "-" * 72)
    print("  Phase 1: Generating RAG Responses for Golden Test Cases")
    print("-" * 72)

    samples = []
    rag_answers = []
    retrieved_contexts_list = []
    generation_times = []

    for i, testcase in enumerate(golden_cases, 1):
        q = testcase["user_input"]
        golden_ref = testcase["reference"]
        ref_contexts = testcase.get("reference_contexts", [])

        print(f"\n[{i}/{len(golden_cases)}] Question: {q}")

        retrieved_docs = retriever.invoke(q)
        retrieved_contexts = [doc.page_content for doc in retrieved_docs]

        start = perf_counter()
        answer = rag_chain.invoke({"reviews": retrieved_docs, "question": q})
        gen_time = perf_counter() - start

        generation_times.append(gen_time)
        rag_answers.append(answer)
        retrieved_contexts_list.append(retrieved_contexts)

        print(f"  RAG Model Answer: {answer[:130]}...")
        print(f"  Golden Reference: {golden_ref[:130]}...")
        print(f"  Inference Time:   {gen_time:.2f}s")

        samples.append(
            SingleTurnSample(
                user_input=q,
                response=answer,
                retrieved_contexts=retrieved_contexts,
                reference=golden_ref,
                reference_contexts=ref_contexts,
            )
        )

    print("\n" + "-" * 72)
    print("  Phase 2: Ragas Quantitative Evaluation (6 Metrics)")
    print("-" * 72)

    client = AsyncOpenAI(base_url=f"{config.OLLAMA_BASE_URL}/v1", api_key="ollama")
    evaluator_llm = llm_factory(config.LLM_MODEL, client=client, mode=Mode.JSON_SCHEMA)
    evaluator_embeddings = OllamaRagasEmbeddings(client=client, model=config.EMBEDDING_MODEL)

    metrics = build_evaluator_metrics(evaluator_llm, evaluator_embeddings)
    dataset = EvaluationDataset(samples=samples)

    eval_start = perf_counter()
    eval_result = evaluate(
        dataset=dataset,
        metrics=metrics,
        llm=evaluator_llm,
        embeddings=evaluator_embeddings,
        raise_exceptions=False,
        show_progress=True,
        run_config=RunConfig(max_workers=1, timeout=300, max_retries=2),
    )
    eval_duration = perf_counter() - eval_start

    results_df = eval_result.to_pandas()

    metric_columns = [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
        "answer_correctness",
        "answer_similarity",
    ]
    present_metrics = [c for c in metric_columns if c in results_df.columns]

    print("\n" + "=" * 72)
    print("  Per-Case Evaluation Scores")
    print("=" * 72)
    for i, row in results_df.iterrows():
        print(f"\n[Case {i + 1}] {row['user_input']}")
        print(f"  RAG Answer:       {row['response'][:100]}...")
        print(f"  Golden Reference: {row.get('reference', '')[:100]}...")
        for m in present_metrics:
            val = row[m]
            val_str = f"{val:.4f}" if isinstance(val, (int, float)) and not np.isnan(val) else "N/A"
            bar = _score_bar(val)
            label = m.replace("_", " ").title()
            print(f"    - {label:<22} {val_str:>6}  {bar}")

    print("\n" + "=" * 72)
    print("  Aggregate Evaluation Summary Across Golden Dataset")
    print("=" * 72)
    print(f"{'Metric':<25} {'Mean':>8} {'Std':>8} {'Min':>8} {'Max':>8}")
    print("-" * 72)
    for m in present_metrics:
        vals = pd.to_numeric(results_df[m], errors="coerce").dropna()
        if not vals.empty:
            label = m.replace("_", " ").title()
            print(
                f"{label:<25} {vals.mean():>8.4f} {vals.std():>8.4f} {vals.min():>8.4f} {vals.max():>8.4f}"
            )
        else:
            print(f"{m:<25} {'N/A':>8}")

    print("-" * 72)
    print(f"Average Inference Time:  {np.mean(generation_times):.2f}s per query")
    print(f"Total Evaluation Time:   {eval_duration:.2f}s")

    output_csv = config.GOLDEN_RESULTS_CSV
    results_df.to_csv(output_csv, index=False)
    print(f"\nDetailed evaluation results exported to: {output_csv}")
    return results_df


def _score_bar(value, width: int = 10) -> str:
    """Render a unit-interval score as a terminal bar.

    Args:
        value: Numeric score expected in [0, 1], or None/float NaN for missing data.
        width: Number of characters for a valid unit-interval score.

    Returns:
        Filled and empty block characters, or only empty blocks for missing data.
        filled is the rounded product of value and width; values are not clamped.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "░" * width
    filled = int(round(value * width))
    return "█" * filled + "░" * (width - filled)


def main():
    """Parse command-line options and run golden dataset evaluation.

    Reads --regenerate to request fresh synthesis and --limit to select the first
    N cases. args stores the parsed options passed to run_golden_evaluation.

    Returns:
        None. Evaluation prints scores and writes the configured results CSV.
    """
    parser = argparse.ArgumentParser(description="Run automated RAG evaluation against a synthetic golden dataset")
    parser.add_argument("--regenerate", action="store_true", help="Force re-generation of synthetic golden testset")
    parser.add_argument("--limit", type=int, default=None, help="Limit to first N test cases")
    args = parser.parse_args()

    run_golden_evaluation(force_regenerate=args.regenerate, limit=args.limit)


if __name__ == "__main__":
    main()
