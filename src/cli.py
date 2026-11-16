"""Provide interactive pizza-review answers with optional Ragas evaluation.

main builds a generation chain and uses the shared retriever for evidence.
The module-level model, template, prompt, and chain retain a directly
importable generation setup; main uses its own build_rag_chain instances.
Importing the shared vector store can initialize and ingest the review index.
"""

from time import perf_counter

import numpy as np
from langchain_ollama.llms import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate

from src.evaluation.evaluator import RAGEvaluator
from src.rag_chain import build_rag_chain
from src.vector_store import retriever


model = OllamaLLM(model="llama3.2", base_url="http://localhost:11434")

template = """
Your are an expert in answering questions about a pizza restaurant

Here are some relevant reviews: {reviews}

Here is the question: {question}
"""

prompt = ChatPromptTemplate.from_template(template)
chain = prompt | model

def main() -> None:
    """Run the interactive question-answering and evaluation session.

    Reads questions from standard input and prints generated answers and timing.
    Commands q, eval on, eval off, summary, and save control the session.

    Returns:
        None. Single-response evaluation history is saved on exit when present.

    Notes:
        retrieved_docs holds review Documents passed to the chain; contexts holds
        their plain text for Ragas. eval_enabled controls scoring. gen_time and
        eval_time measure generation and evaluation separately in seconds.
        Ollama and the configured vector store must be available.
    """
    prompt, model_instance, chain_instance = build_rag_chain()
    evaluator = RAGEvaluator()
    eval_enabled = True

    print("Pizza Review RAG Assistant (with RAGAS evaluation)")
    print("Commands:  'q' = quit  |  'eval off/on' = toggle evaluation  |  'summary' = show stats")
    print("-" * 60)

    while True:
        question = input("\nEnter your question: ")

        if question.strip().lower() == "q":
            break

        if question.strip().lower() == "eval off":
            eval_enabled = False
            print("✗ Evaluation disabled")
            continue

        if question.strip().lower() == "eval on":
            eval_enabled = True
            print("✓ Evaluation enabled")
            continue

        if question.strip().lower() == "summary":
            summary = evaluator.get_summary()
            if summary:
                print("\n── Aggregate Evaluation Summary ──")
                for metric, stats in summary.items():
                    label = metric.replace("_", " ").title()
                    print(f"  {label}: mean={stats['mean']:.4f}  min={stats['min']:.4f}  max={stats['max']:.4f}  (n={stats['count']})")
            else:
                print("No evaluations recorded yet.")
            continue

        if question.strip().lower() == "save":
            evaluator.save_history()
            continue

        retrieved_docs = retriever.invoke(question)
        contexts = [doc.page_content for doc in retrieved_docs]

        start = perf_counter()
        result = chain_instance.invoke({"reviews": retrieved_docs, "question": question})
        gen_time = perf_counter() - start

        print(f"\nAnswer: {result}")
        print(f"Time taken: {np.round(gen_time, 2)} seconds")

        if eval_enabled:
            print("\nEvaluating response quality...")
            eval_start = perf_counter()
            scores = evaluator.evaluate_single(
                question=question,
                answer=result,
                contexts=contexts,
            )
            eval_time = perf_counter() - eval_start
            evaluator.print_scores(scores)
            print(f"Evaluation time: {np.round(eval_time, 2)} seconds")

    if evaluator._history:
        evaluator.save_history()
        summary = evaluator.get_summary()
        if summary:
            print("\n── Final Session Summary ──")
            for metric, stats in summary.items():
                label = metric.replace("_", " ").title()
                print(f"  {label}: mean={stats['mean']:.4f}  (n={stats['count']})")


if __name__ == "__main__":
    main()
