"""Serve the local PizzaLover UI, streamed RAG answers, ingestion, and Ragas.

Run one worker to keep evaluation history and resource locks in one session.
All inference calls use the repository's configured Ollama endpoint.
"""

import asyncio
import csv
import json
import logging
import math
import sqlite3
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

import config
from api.schemas import (ChatRequest, GoogleIngestRequest, RagasScores, Review, LoginRequest,
                         SetupRequest, UserCreateRequest, UserUpdateRequest, RoleRequest, SQLRequest, FeedbackRequest)
from src.chat_history import ChatHistory
from src.access import Access, PERMISSIONS
from src.pipeline import Pipeline

logger = logging.getLogger(__name__)
STATIC_DIR = config.PROJECT_ROOT / "static"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
PROFILE_COOKIE = "pizzalover_session"


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
    app.state.chat_history = await asyncio.to_thread(ChatHistory, config.CHAT_HISTORY_PATH)
    app.state.access = await asyncio.to_thread(Access, app.state.chat_history)
    app.state.pipeline = await asyncio.to_thread(Pipeline, config.STAGING_PATH)
    app.state.pipeline_lock = asyncio.Lock()
    if await asyncio.to_thread(app.state.access.setup_required):
        logger.warning("PizzaLover account setup token: %s", app.state.access.setup_token)
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


def set_profile_cookie(response, token, request):
    response.set_cookie(PROFILE_COOKIE, token, httponly=True, samesite="strict",
                        secure=request.url.scheme == 'https', max_age=7 * 86400)


async def current_profile(request):
    profile = await asyncio.to_thread(request.app.state.access.current, request.cookies.get(PROFILE_COOKIE, ""))
    if not profile:
        raise HTTPException(401, "Sign in first")
    expected = request.headers.get("X-Profile-ID")
    if expected and expected != profile['id']:
        raise HTTPException(409, "The account changed in another tab. Reload before continuing.")
    return profile


@app.middleware("http")
async def authorize_request(request: Request, call_next):
    path = request.url.path
    if path.startswith('/api/'):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail': 'Cross-origin requests are forbidden'}, status_code=403)
        public = path in ('/api/auth/status', '/api/auth/setup', '/api/auth/login')
        if not public:
            try:
                user = await current_profile(request)
            except HTTPException as exc:
                return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
            permission = None
            if path.startswith(('/api/chat', '/api/metadata')):
                permission = 'chat:use'
            elif path.startswith('/api/evaluation'):
                permission = 'scorecards:view'
            elif path.startswith(('/api/pipeline', '/api/ingest')):
                permission = 'pipeline:manage'
            elif path.startswith('/api/roles'):
                permission = 'roles:manage'
            elif path.startswith('/api/feedback'):
                permission = 'feedback:submit'
            elif path.startswith('/api/users') or (path == '/api/profiles' and request.method == 'POST'):
                permission = 'users:create'
            if permission and permission not in user['permissions']:
                return JSONResponse({'detail': f'Missing permission: {permission}'}, status_code=403)
    response = await call_next(request)
    if path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


def access_error(exc):
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(404, exc.args[0])
    if isinstance(exc, sqlite3.IntegrityError):
        return HTTPException(409, 'That name or ID already exists, or the role is in use')
    return HTTPException(422, str(exc))


@app.get('/api/auth/status')
async def auth_status(request: Request):
    return {'setup_required': await asyncio.to_thread(request.app.state.access.setup_required)}


@app.post('/api/auth/setup', status_code=201)
async def setup(body: SetupRequest, request: Request, response: Response):
    try:
        user = await asyncio.to_thread(request.app.state.access.setup, body.token, body.name.strip(), body.password)
    except (ValueError, PermissionError, sqlite3.IntegrityError) as exc:
        raise access_error(exc) from exc
    token = await asyncio.to_thread(request.app.state.access.session, user['id'])
    set_profile_cookie(response, token, request)
    return user


@app.post('/api/auth/login')
async def login(body: LoginRequest, request: Request, response: Response):
    try:
        user = await asyncio.to_thread(request.app.state.access.login, body.name.strip(), body.password)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    token = await asyncio.to_thread(request.app.state.access.session, user['id'])
    set_profile_cookie(response, token, request)
    return user


@app.post('/api/auth/logout', status_code=204)
async def logout(request: Request, response: Response):
    await asyncio.to_thread(request.app.state.access.logout, request.cookies.get(PROFILE_COOKIE, ''))
    response.delete_cookie(PROFILE_COOKIE)


@app.get('/api/profiles')
async def profiles(request: Request, response: Response):
    user = await current_profile(request)
    response.headers['Cache-Control'] = 'no-store'
    return {'current': user, 'profiles': [user]}


@app.post('/api/profiles', status_code=201)
@app.post('/api/users', status_code=201)
async def create_profile(body: UserCreateRequest, request: Request):
    user = await current_profile(request)
    if body.role != 'USER' and 'roles:manage' not in user['permissions']:
        raise HTTPException(403, 'roles:manage is required to assign roles')
    try:
        return await asyncio.to_thread(request.app.state.access.create_user, body.name, body.password, body.role)
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise access_error(exc) from exc


@app.post('/api/profiles/{profile_id}/select')
async def select_profile(profile_id: UUID):
    raise HTTPException(403, 'Sign out and sign in with the account password to switch accounts')


@app.get('/api/users')
async def users(request: Request):
    return {'users': await asyncio.to_thread(request.app.state.access.users)}


@app.patch('/api/users/{user_id}')
async def update_user(user_id: UUID, body: UserUpdateRequest, request: Request):
    actor = await current_profile(request)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if 'roles:manage' not in actor['permissions']:
        raise HTTPException(403, 'roles:manage is required to update existing accounts')
    try:
        return await asyncio.to_thread(request.app.state.access.update_user, str(user_id), changes)
    except (KeyError, ValueError, sqlite3.IntegrityError) as exc:
        raise access_error(exc) from exc


@app.get('/api/roles')
async def roles(request: Request):
    return {'permissions': PERMISSIONS, 'roles': await asyncio.to_thread(request.app.state.access.roles)}


@app.post('/api/roles', status_code=201)
async def create_role(body: RoleRequest, request: Request):
    try:
        await asyncio.to_thread(request.app.state.access.save_role, body.name, body.permissions, True)
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise access_error(exc) from exc
    return body


@app.put('/api/roles/{name}')
async def update_role(name: str, body: RoleRequest, request: Request):
    if name != body.name:
        raise HTTPException(422, 'Role names must match')
    try:
        await asyncio.to_thread(request.app.state.access.save_role, name, body.permissions)
    except (KeyError, ValueError, sqlite3.IntegrityError) as exc:
        raise access_error(exc) from exc
    return body


@app.delete('/api/roles/{name}', status_code=204)
async def delete_role(name: str, request: Request):
    try:
        await asyncio.to_thread(request.app.state.access.delete_role, name)
    except (KeyError, ValueError, sqlite3.IntegrityError) as exc:
        raise access_error(exc) from exc


@app.post('/api/feedback', status_code=201)
async def feedback(body: FeedbackRequest, request: Request):
    user = await current_profile(request)
    try:
        return await asyncio.to_thread(request.app.state.access.feedback, user, str(body.message_id), body.rating, body.comment)
    except (KeyError, ValueError) as exc:
        raise access_error(exc) from exc


@app.get("/api/chats")
async def list_chats(request: Request, response: Response):
    profile = await current_profile(request)
    response.headers["Cache-Control"] = "no-store"
    return {"chats": await asyncio.to_thread(request.app.state.chat_history.chats, profile["id"])}


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: UUID, request: Request, response: Response):
    profile = await current_profile(request)
    result = await asyncio.to_thread(request.app.state.chat_history.chat, profile["id"], str(chat_id))
    if not result:
        raise HTTPException(404, "Chat not found for this profile")
    response.headers["Cache-Control"] = "no-store"
    if 'scorecards:view' not in profile['permissions']:
        for turn in result['turns']:
            if turn.get('metrics'):
                turn['metrics']['scores'] = None
    saved = await asyncio.to_thread(request.app.state.access.saved_feedback, profile['id'], str(chat_id))
    for turn in result['turns']:
        turn['feedback'] = saved.get(turn['id'])
    return result


@app.delete("/api/chats/{chat_id}", status_code=204)
async def delete_chat(chat_id: UUID, request: Request):
    profile = await current_profile(request)
    deleted = await asyncio.to_thread(request.app.state.chat_history.delete_chat, profile["id"], str(chat_id))
    if not deleted:
        raise HTTPException(404, "Chat not found for this profile")


@app.post("/api/chat/stream")
async def chat(body: ChatRequest, request: Request):
    """Stream citations, answer chunks, timings/scores, and an explicit done event.

    Retrieval honors the request's metadata filters. Evaluation starts only
    after all answer chunks, runs in a worker thread, and shares the model lock.
    Failures emit an error event followed by done without inventing an answer.
    """
    from src.vector_store import get_retriever
    engine = request.app.state.engine
    history = request.app.state.chat_history
    profile = await current_profile(request)
    # Evaluation scores must not leak through SSE or saved conversation payloads.
    body.eval_enabled = body.eval_enabled and 'scorecards:view' in profile['permissions']
    if body.chat_id:
        chat_id = str(body.chat_id)
    else:
        saved_chat = await asyncio.to_thread(history.create_chat, profile["id"], body.question.strip()[:100])
        chat_id = saved_chat["id"]
    try:
        turn = await asyncio.to_thread(history.start_turn, profile["id"], chat_id, body.question,
                                      body.filters.model_dump(), body.eval_enabled)
    except KeyError as exc:
        raise HTTPException(404, "Chat not found for this profile") from exc

    async def frames():
        """Produce a single turn, with SSE heartbeats during lengthy judge work."""
        try:
            async with request.app.state.model_lock:
                retriever = get_retriever(vector_store=engine.store, **body.filters.model_dump())
                docs = await finish_thread(retriever.invoke, body.question)
                citations = [dict(doc.metadata, Comment=doc.page_content) for doc in docs]
                turn["citations"] = citations
                yield event("citations", citations)
                start = perf_counter()
                answer = ""
                if not docs:
                    answer = "No reviews match these filters. Try broadening your search."
                    turn["text"] = answer
                    yield event("token", answer)
                else:
                    async for token in engine.chain.astream({"question": body.question, "reviews": docs}):
                        if await request.is_disconnected():
                            return
                        answer += token
                        turn["text"] = answer
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
                turn.update(metrics=metrics, status="complete", error=None)
                await finish_thread(history.save_turn, profile["id"], chat_id, turn)
                yield event("metrics", metrics)
        except Exception:
            logger.exception("Chat stream failed")
            turn.update(status="failed", error="Could not complete this turn. Check the local Ollama server and required models.")
            yield event("error", {"message": turn["error"]})
        finally:
            # Starlette cancels the generator on disconnect; shield the final DB write.
            await finish_thread(history.save_turn, profile["id"], chat_id, turn)
        yield event("done", {})

    response = StreamingResponse(frames(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no",
        "X-Chat-ID": chat_id, "X-Turn-ID": turn["id"]})
    return response


@app.get("/api/metadata/options")
async def metadata(request: Request):
    """Return distinct indexed franchise/location values and document count."""
    records = await asyncio.to_thread(request.app.state.engine.store.get, include=["metadatas"])
    rows = records["metadatas"] or []
    return {"franchises": sorted({r["Franchise"] for r in rows if r.get("Franchise")}),
            "locations": sorted({r["Location"] for r in rows if r.get("Location")}),
            "total_documents": len(records["ids"])}


async def read_reviews(request, inspect=False):
    """Parse bounded JSON or multipart CSV/XLSX, validating the entire batch first."""
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Upload must be smaller than 2 MiB")
    # Starlette caches body for subsequent JSON/form parsing.
    request._body = bytes(raw)
    is_file = request.headers.get("content-type", "").startswith("multipart/form-data")
    request.state.skipped_blank_comments = []
    try:
        if is_file:
            async with request.form() as form:
                upload = form.get("file")
                if upload is None or not hasattr(upload, "read"):
                    raise HTTPException(422, "Provide a CSV or XLSX upload in the 'file' field")
                from src.review_files import parse_review_file
                mapping_text = form.get("mapping")
                mapping = json.loads(mapping_text) if mapping_text is not None else None
                rows = await asyncio.to_thread(parse_review_file, upload.filename, await upload.read(), mapping, inspect)
                if inspect:
                    return rows
        else:
            if inspect:
                raise HTTPException(422, "Provide a CSV or XLSX file")
            payload = await request.json()
            rows = payload if isinstance(payload, list) else [payload]
        if not rows or len(rows) > 500:
            raise HTTPException(422, "Provide between 1 and 500 reviews")
        reviews = []
        for index, row in enumerate(rows, start=1):
            comment = row.get("Comment") if isinstance(row, dict) else None
            if is_file and (comment is None or (isinstance(comment, str) and not comment.strip())):
                request.state.skipped_blank_comments.append(index)
                continue
            try:
                reviews.append(Review.model_validate(row))
            except ValidationError as exc:
                issues = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
                raise HTTPException(422, f"Review {index}: {issues}. Correct the file or column mapping and retry.") from exc
        if not reviews:
            raise HTTPException(422, "No reviews have comment text. Check the column mapped to Comment or provide a file with written reviews.")
        ids = [r.Feedback_ID for r in reviews]
        if len(set(ids)) != len(ids):
            raise HTTPException(409, "Feedback_ID values must be unique within an upload")
        return reviews
    except (ValidationError, ValueError, UnicodeError, csv.Error) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/ingest/preview")
async def preview_ingestion(request: Request):
    """Inspect bounded file columns and samples without embedding or storing data."""
    return await read_reviews(request, inspect=True)


@app.post("/api/ingest")
async def ingest(request: Request):
    """Persist new review IDs after JSON/CSV/XLSX validation; reject existing IDs."""
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
    result = {"added": len(reviews)}
    if request.state.skipped_blank_comments:
        result["skipped_blank_comments"] = len(request.state.skipped_blank_comments)
        result["skipped_reviews"] = request.state.skipped_blank_comments
    return result


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


@app.get('/api/pipeline')
async def pipeline_preview(request: Request):
    async with request.app.state.pipeline_lock:
        return await asyncio.to_thread(request.app.state.pipeline.query, 'SELECT * FROM staging_reviews LIMIT 500')


@app.post('/api/pipeline/stage', status_code=201)
async def stage_reviews(request: Request):
    reviews = await read_reviews(request)
    async with request.app.state.pipeline_lock:
        try:
            count = await finish_thread(request.app.state.pipeline.stage, reviews)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, 'A Feedback_ID is already staged; edit or delete that row first') from exc
    return {'staged': count, 'skipped_blank_comments': len(request.state.skipped_blank_comments)}


@app.post('/api/pipeline/query')
async def pipeline_query(body: SQLRequest, request: Request):
    async with request.app.state.pipeline_lock:
        try:
            return await finish_thread(request.app.state.pipeline.query, body.sql)
        except sqlite3.Error as exc:
            raise HTTPException(422, f'SQL rejected: {exc}. Use SELECT, INSERT, UPDATE or DELETE on staging_reviews only.') from exc


@app.post('/api/pipeline/embed')
async def pipeline_embed(request: Request):
    from langchain_core.documents import Document
    # Keep the staging snapshot unchanged until embedding and clearing complete.
    async with request.app.state.pipeline_lock:
        rows = await asyncio.to_thread(request.app.state.pipeline.rows)
        if not rows:
            raise HTTPException(422, 'No staged reviews to embed')
        if len(rows) > 500:
            raise HTTPException(422, 'Embed at most 500 staged reviews at a time; remove extra rows first')
        try:
            reviews = [Review.model_validate(row) for row in rows]
        except ValidationError as exc:
            raise HTTPException(422, f'Staged data is invalid; correct it before embedding: {exc}') from exc
        ids = [review.Feedback_ID for review in reviews]
        if len(set(ids)) != len(ids):
            raise HTTPException(422, 'Staged Feedback_ID values must be unique after whitespace normalization')
        store = request.app.state.engine.store
        async with request.app.state.model_lock:
            def embed_and_clear():
                existing = store.get(include=['metadatas'])
                known = {row.get('Feedback_ID') for row in existing['metadatas'] or []}
                if any(r.Feedback_ID in known for r in reviews):
                    raise HTTPException(409, 'A staged Feedback_ID is already indexed. Remove it from staging before embedding.')
                docs = [Document(page_content=r.Comment, metadata=r.model_dump(exclude={'Comment'})) for r in reviews]
                try:
                    store.add_documents(docs, ids=[r.Feedback_ID for r in reviews])
                except Exception as exc:
                    logger.exception('Staged embedding failed')
                    raise HTTPException(503, 'Embedding failed; staged rows are retained. Check Ollama and retry. If some IDs were indexed, remove those staged IDs first.') from exc
                request.app.state.pipeline.clear()
            # Complete the successful write and cleanup even if the browser disconnects.
            await finish_thread(embed_and_clear)
    return {'added': len(reviews)}


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
