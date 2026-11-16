"""Evaluate RAG responses through Ollama-backed Ragas judges and embeddings.

The metric suite checks faithfulness, answer relevancy, reference-free context
precision, and context utilization. RAGEvaluator supports single and batch
scoring; only single-response calls populate its in-memory history for JSON
export and aggregate summaries.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from openai import AsyncOpenAI
from ragas import EvaluationDataset, SingleTurnSample, evaluate
from ragas.llms import llm_factory
from ragas.metrics._answer_relevance import AnswerRelevancy
from ragas.metrics._context_precision import (
    ContextUtilization,
    LLMContextPrecisionWithoutReference,
)
from ragas.metrics._faithfulness import Faithfulness

import config
from src.evaluation.embeddings_adapter import OllamaRagasEmbeddings


class RAGEvaluator:
    """Score pizza RAG responses with four reference-free Ragas metrics.

    Attributes:
        client: Async OpenAI-compatible client targeting Ollama's /v1 endpoint.
        evaluator_llm: Ragas judge for faithfulness, relevancy, and context metrics.
        evaluator_embeddings: Adapter used to compare question/answer embeddings.
        metrics: Faithfulness, answer relevancy, context precision without a
            reference, and context utilization instances.
        _history: Timestamped single-response records, with questions, answers
            truncated to 200 characters, and scores. Batch calls do not add records.
    """

    def __init__(
        self,
        llm_model: str = config.LLM_MODEL,
        embedding_model: str = config.EMBEDDING_MODEL,
        ollama_base_url: str = config.OLLAMA_BASE_URL,
    ):
        # ── OpenAI-compatible async client pointing at Ollama ──
        """Configure Ollama clients, the metric suite, and empty session history.

        Args:
            llm_model: Ollama model identifier used by the Ragas judge.
            embedding_model: Ollama embedding model used by answer relevancy.
            ollama_base_url: Server base URL; /v1 is appended for API compatibility.
        """
        self.client = AsyncOpenAI(
            base_url=f"{ollama_base_url}/v1",
            api_key="ollama",
        )

        # ── evaluator LLM (used by RAGAS to judge faithfulness, etc.) ──
        self.evaluator_llm = llm_factory(llm_model, client=self.client)

        # ── evaluator embeddings (used by answer-relevancy metric) ──
        self.evaluator_embeddings = OllamaRagasEmbeddings(
            client=self.client,
            model=embedding_model,
        )

        # ── metrics suite (compatible with ragas.evaluate) ──
        f = Faithfulness()
        f.llm = self.evaluator_llm

        ar = AnswerRelevancy()
        ar.llm = self.evaluator_llm
        ar.embeddings = self.evaluator_embeddings

        cp = LLMContextPrecisionWithoutReference()
        cp.llm = self.evaluator_llm

        cu = ContextUtilization()
        cu.llm = self.evaluator_llm

        self.metrics = [f, ar, cp, cu]

        # ── history for batch reporting ──
        self._history: list[dict] = []

    def evaluate_single(
        self,
        question: str,
        answer: str,
        contexts: list[str],
        reference: Optional[str] = None,
    ) -> dict:
        """Score one response and append a timestamped record to session history.

        Args:
            question: Original user question.
            answer: Generated response to evaluate.
            contexts: Retrieved review texts supplied as evidence to the model.
            reference: Optional expected answer attached to the Ragas sample.

        Returns:
            Metric-name-to-score mapping. Float scores are rounded to four decimal
            places and float NaN values become None. Sample input fields are excluded.

        Notes:
            sample packages the interaction; dataset wraps it for Ragas. Metric
            exceptions are suppressed by Ragas evaluation. record retains a shortened
            answer for later history export and aggregate reporting.
        """
        sample = SingleTurnSample(
            user_input=question,
            response=answer,
            retrieved_contexts=contexts,
            reference=reference,
        )
        dataset = EvaluationDataset(samples=[sample])

        result = evaluate(
            dataset=dataset,
            metrics=self.metrics,
            llm=self.evaluator_llm,
            embeddings=self.evaluator_embeddings,
            raise_exceptions=False,
            show_progress=False,
        )

        scores = {
            col: result.to_pandas()[col].iloc[0]
            for col in result.to_pandas().columns
            if col not in ("user_input", "response", "retrieved_contexts", "reference")
        }

        # Replace any NaN with None for cleaner display
        scores = {
            k: (None if isinstance(v, float) and np.isnan(v) else round(v, 4) if isinstance(v, float) else v)
            for k, v in scores.items()
        }

        record = {
            "timestamp": datetime.now().isoformat(),
            "question": question,
            "answer": answer[:200],
            "scores": scores,
        }
        self._history.append(record)
        return scores

    def evaluate_batch(
        self,
        questions: list[str],
        answers: list[str],
        contexts_list: list[list[str]],
        references: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        """Score parallel lists of interactions without changing session history.

        Args:
            questions: User questions in evaluation order.
            answers: Generated answers aligned with questions.
            contexts_list: Lists of retrieved review texts, one list per question.
            references: Optional expected answers aligned with the other lists.

        Returns:
            A pandas DataFrame with sample fields and metric scores, including NaN
            for metrics that fail under Ragas' suppressed-exception evaluation.

        Notes:
            samples is built by zipping the three required lists, so unequal lengths
            are truncated to the shortest. Supply matching lengths; a nonempty
            references list must contain an entry for every constructed sample.
        """
        samples = []
        for i, (q, a, ctx) in enumerate(zip(questions, answers, contexts_list)):
            ref = references[i] if references else None
            samples.append(
                SingleTurnSample(
                    user_input=q,
                    response=a,
                    retrieved_contexts=ctx,
                    reference=ref,
                )
            )

        dataset = EvaluationDataset(samples=samples)
        result = evaluate(
            dataset=dataset,
            metrics=self.metrics,
            llm=self.evaluator_llm,
            embeddings=self.evaluator_embeddings,
            raise_exceptions=False,
            show_progress=True,
        )
        return result.to_pandas()

    def print_scores(self, scores: dict) -> None:
        """Print each metric's label, numeric score, and terminal bar.

        Args:
            scores: Mapping of metric names to numeric values, None, or float NaN.

        Returns:
            None. Missing values are displayed as N/A with an empty score bar.
        """
        print("\nRAGAS Evaluation Scores")
        print("-" * 72)
        for metric, value in scores.items():
            label = self._metric_label(metric)
            if value is None or (isinstance(value, float) and np.isnan(value)):
                val_str = "N/A"
                bar = self._score_bar(value)
            else:
                val_str = f"{float(value):.4f}"
                bar = self._score_bar(float(value))
            print(f"  {label:<35} {val_str:>7}  {bar}")
        print("-" * 72)

    @staticmethod
    def _metric_label(metric: str) -> str:
        """Convert a Ragas metric identifier into a readable terminal label.

        Args:
            metric: Metric identifier, optionally containing underscores or hyphens.

        Returns:
            A known alias when available, otherwise the original name with underscores
            replaced by spaces and title casing applied. normalized is used to look
            up aliases without case, surrounding whitespace, or hyphen differences.
        """
        normalized = metric.strip().lower().replace("-", "_")
        aliases = {
            "faithfulness": "Faithfulness",
            "answer_relevancy": "Answer Relevancy",
            "answer_relevance": "Answer Relevancy",
            "context_precision": "Context Precision",
            "llm_context_precision_without_reference": "Context Precision",
            "context_utilization": "Context Utilization",
            "context_recall": "Context Recall",
            "answer_correctness": "Answer Correctness",
            "answer_similarity": "Answer Similarity",
        }
        return aliases.get(normalized, metric.replace("_", " ").title())

    def save_history(self, filepath: Path = config.EVALUATION_HISTORY_PATH) -> None:
        """Write the current single-response session history as indented JSON.

        Args:
            filepath: Destination file; its parent directory must already exist.

        Returns:
            None. Overwrites an existing file and prints its destination.

        Notes:
            Values unsupported by JSON are converted to strings. History remains in
            memory after saving; existing on-disk history is not merged.
        """
        with open(filepath, "w") as f:
            json.dump(self._history, f, indent=2, default=str)
        print(f"Evaluation history saved to {filepath}")

    def get_summary(self) -> dict:
        """Aggregate numeric scores from single-response session history.

        Returns:
            A mapping from metric names to mean, min, max, and count. Numeric
            aggregates are rounded to four decimal places. Returns an empty mapping
            when history is empty or contains no numeric scores.

        Notes:
            all_scores groups numeric values by metric; missing values are skipped.
            Batch evaluation results are not included in this session summary.
        """
        if not self._history:
            return {}
        all_scores: dict[str, list[float]] = {}
        for record in self._history:
            for metric, value in record["scores"].items():
                if isinstance(value, (int, float)):
                    all_scores.setdefault(metric, []).append(value)
        return {
            metric: {
                "mean": round(float(np.mean(vals)), 4),
                "min": round(float(np.min(vals)), 4),
                "max": round(float(np.max(vals)), 4),
                "count": len(vals),
            }
            for metric, vals in all_scores.items()
        }

    @staticmethod
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
