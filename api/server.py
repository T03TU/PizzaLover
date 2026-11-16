"""Serve the local PizzaLover UI, streamed RAG answers, ingestion, and Ragas.

Run one worker to keep evaluation history and resource locks in one session.
All inference calls use the repository's configured Ollama endpoint.
"""

import asyncio
import csv
import io
import json
import logging
import math
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

import config
from api.schemas import ChatRequest, GoogleIngestRequest, RagasScores, Review

logger = logging.getLogger(__name__)
STATIC_DIR = config.PROJECT_ROOT / "static"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024


def create_engine():
    """Construct the shared Chroma, chain, and judge once at application startup."""
    from types import SimpleNamespace
    from src.evaluation.evaluator import RAGEvaluator
    from src.rag_chain import build_rag_chain
    from src.vector_store import get_embeddings, init_vector_store

    embeddings = get_embeddings()
    store = init_vector_store(embeddings=embeddings)
    _, model, chain = build_rag_chain()
    return SimpleNamespace(store=store, model=model, chain=chain, evaluator=RAGEvaluator())


@asynccontextmanager
async def lifespan(app):
    """Initialize resources off the event loop and close the judge's HTTP client."""
    app.state.engine = await asyncio.to_thread(create_engine)
    # One lock serializes generation, ingestion embeddings, and judge work on Ollama.
    app.state.model_lock = asyncio.Lock()
    try:
        yield
    finally:
        await app.state.engine.evaluator.client.close()


app = FastAPI(title="PizzaLover", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    """Return the buildless local web client."""
    return FileResponse(STATIC_DIR / "index.html")


def event(name, data):
    """Encode a JSON SSE frame; JSON escaping keeps newlines within one data line."""
    return f"event: {name}\ndata: {json.dumps(data, allow_nan=False)}\n\n"


def normalize_scores(scores):
    """Normalize Ragas precision aliases and nonfinite values for JSON clients."""
    result = {}
    for key, value in scores.items():
        if key == "llm_context_precision_without_reference":
            key = "context_precision"
        result[key] = float(value) if value is not None and math.isfinite(float(value)) else None
    return RagasScores(**result).model_dump()


async def finish_thread(function, *args, **kwargs):
    """Keep a model lock held until synchronous work ends, even on disconnect."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


@app.post("/api/chat/stream")
async def chat(body: ChatRequest, request: Request):
    """Stream citations, answer chunks, timings/scores, and an explicit done event.

    Retrieval honors the request's metadata filters. Evaluation starts only
    after all answer chunks, runs in a worker thread, and shares the model lock.
    Failures emit an error event followed by done without inventing an answer.
    """
    from src.vector_store import get_retriever
    engine = request.app.state.engine

    async def frames():
        """Produce a single turn, with SSE heartbeats during lengthy judge work."""
        try:
            async with request.app.state.model_lock:
                retriever = get_retriever(vector_store=engine.store, **body.filters.model_dump())
                docs = await finish_thread(retriever.invoke, body.question)
                citations = [dict(doc.metadata, Comment=doc.page_content) for doc in docs]
                yield event("citations", citations)
                start = perf_counter()
                answer = ""
                if not docs:
                    answer = "No reviews match these filters. Try broadening your search."
                    yield event("token", answer)
                else:
                    async for token in engine.chain.astream({"question": body.question, "reviews": docs}):
                        if await request.is_disconnected():
                            return
                        answer += token
                        yield event("token", token)
                generation_time = perf_counter() - start
                metrics = {"generation_time": generation_time, "evaluation_time": 0,
                           "scores": None, "evaluation_status": "disabled"}
                if body.eval_enabled and docs:
                    yield ": evaluating\n\n"
                    eval_start = perf_counter()
                    task = asyncio.create_task(finish_thread(
                        engine.evaluator.evaluate_single, question=body.question,
                        answer=answer, contexts=[doc.page_content for doc in docs]))
                    try:
                        while not task.done():
                            await asyncio.wait({task}, timeout=10)
                            if not task.done():
                                yield ": evaluating\n\n"
                        metrics["scores"] = normalize_scores(task.result())
                        metrics["evaluation_status"] = ("partial" if any(value is None for value in metrics["scores"].values()) else "complete")
                    except asyncio.CancelledError:
                        await task
                        raise
                    except Exception:
                        logger.exception("Ragas evaluation failed")
                        metrics["evaluation_status"] = "failed"
                    metrics["evaluation_time"] = perf_counter() - eval_start
                elif not docs:
                    metrics["evaluation_status"] = "no_context"
                yield event("metrics", metrics)
        except Exception:
            logger.exception("Chat stream failed")
            yield event("error", {"message": "Could not complete this turn. Check the local Ollama server and required models."})
        yield event("done", {})

    return StreamingResponse(frames(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/metadata/options")
async def metadata(request: Request):
    """Return distinct indexed franchise/location values and document count."""
    records = await asyncio.to_thread(request.app.state.engine.store.get, include=["metadatas"])
    rows = records["metadatas"] or []
    return {"franchises": sorted({r["Franchise"] for r in rows if r.get("Franchise")}),
            "locations": sorted({r["Location"] for r in rows if r.get("Location")}),
            "total_documents": len(records["ids"])}


async def read_reviews(request):
    """Parse bounded JSON or multipart CSV, validating the entire batch first."""
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Upload must be smaller than 2 MiB")
    # Starlette caches body for subsequent JSON/form parsing.
    request._body = bytes(raw)
    try:
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            async with request.form() as form:
                upload = form.get("file")
                if upload is None or not hasattr(upload, "read"):
                    raise HTTPException(422, "Provide a CSV upload in the 'file' field")
                text = (await upload.read()).decode("utf-8-sig")
                rows = list(csv.DictReader(io.StringIO(text)))
        else:
            payload = await request.json()
            rows = payload if isinstance(payload, list) else [payload]
        if not rows or len(rows) > 500:
            raise HTTPException(422, "Provide between 1 and 500 reviews")
        reviews = [Review.model_validate(row) for row in rows]
        ids = [r.Feedback_ID for r in reviews]
        if len(set(ids)) != len(ids):
            raise HTTPException(409, "Feedback_ID values must be unique within an upload")
        return reviews
    except (ValidationError, ValueError, UnicodeError, csv.Error) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/ingest")
async def ingest(request: Request):
    """Persist new review IDs after JSON/CSV validation; reject existing IDs."""
    from langchain_core.documents import Document
    reviews = await read_reviews(request)
    store = request.app.state.engine.store
    async with request.app.state.model_lock:
        existing = await asyncio.to_thread(store.get, include=["metadatas"])
        known = {row.get("Feedback_ID") for row in existing["metadatas"] or []}
        if any(review.Feedback_ID in known for review in reviews):
            raise HTTPException(409, "A Feedback_ID already exists; use new review IDs")
        docs = [Document(page_content=r.Comment, metadata=r.model_dump(exclude={"Comment"})) for r in reviews]
        try:
            await finish_thread(store.add_documents, docs, ids=[r.Feedback_ID for r in reviews])
        except Exception as exc:
            logger.exception("Review ingestion failed")
            raise HTTPException(503, "Embedding or storage failed; check Ollama and retry") from exc
    return {"added": len(reviews)}


@app.post("/api/ingest/google")
async def ingest_google(body: GoogleIngestRequest, request: Request):
    """Import Google search results, skipping review IDs already indexed."""
    from langchain_core.documents import Document
    from src.google_reviews import GoogleReviewsError, fetch_google_reviews
    try:
        reviews, places, skipped = await fetch_google_reviews(body.country_code, body.region, body.max_places)
    except GoogleReviewsError as exc:
        raise HTTPException(503, str(exc)) from exc
    store = request.app.state.engine.store
    async with request.app.state.model_lock:
        existing = await asyncio.to_thread(store.get, include=["metadatas"])
        known = {row.get("Feedback_ID") for row in existing["metadatas"] or []}
        fresh = [r for r in reviews if r.Feedback_ID not in known]
        if fresh:
            docs = [Document(page_content=r.Comment, metadata=r.model_dump(exclude={"Comment"})) for r in fresh]
            try:
                await finish_thread(store.add_documents, docs, ids=[r.Feedback_ID for r in fresh])
            except Exception as exc:
                logger.exception("Google review ingestion failed")
                raise HTTPException(503, "Embedding or storage failed; check Ollama and retry") from exc
    return {"added": len(fresh), "duplicates": len(reviews) - len(fresh), "places": places, "skipped": skipped}


def golden_summary():
    """Read numeric aggregate metrics from the latest golden report, if present."""
    path = config.GOLDEN_RESULTS_CSV
    if not path.exists():
        return {}
    with path.open(newline="") as file:
        rows = list(csv.DictReader(file))
    summary = {}
    for metric in (*RagasScores.model_fields, "answer_correctness", "context_recall", "answer_similarity"):
        values = []
        for row in rows:
            try:
                value = float(row.get(metric, ""))
                if math.isfinite(value):
                    values.append(value)
            except (ValueError, TypeError):
                pass
        if values:
            summary[metric] = {"mean": sum(values) / len(values), "min": min(values),
                               "max": max(values), "count": len(values)}
    return summary


@app.get("/api/evaluation/summary")
async def summary(request: Request):
    """Return web-session aggregates plus saved golden benchmark aggregates."""
    async with request.app.state.model_lock:
        session = request.app.state.engine.evaluator.get_summary()
    if "llm_context_precision_without_reference" in session:
        session["context_precision"] = session.pop("llm_context_precision_without_reference")
    return {"session": session, "golden": await asyncio.to_thread(golden_summary)}


@app.post("/api/evaluation/save")
async def save(request: Request):
    """Overwrite the configured history JSON with this server's session records."""
    async with request.app.state.model_lock:
        evaluator = request.app.state.engine.evaluator
        await asyncio.to_thread(evaluator.save_history, config.EVALUATION_HISTORY_PATH)
        return {"count": len(evaluator._history), "path": str(config.EVALUATION_HISTORY_PATH)}
