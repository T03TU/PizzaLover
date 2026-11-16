"""HTTP contract and engine compatibility tests with no model or database writes."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.documents import Document

from api.server import app
from src.rag_chain import build_rag_chain, format_reviews
from src.vector_store import get_retriever

REVIEW = {"Feedback_ID": "FB-014", "Franchise": "Sals", "Location": "Downtown",
          "Rating": 5, "Comment": "Excellent pepperoni."}


class FakeStore:
    """Apply Chroma-style clauses in memory and capture retrieval arguments."""

    def __init__(self):
        """Start with a single known source."""
        self.rows = [dict(REVIEW)]
        self.kwargs = None

    def get(self, **kwargs):
        """Return the indexed metadata contract used by the API."""
        return {"ids": [r["Feedback_ID"] for r in self.rows],
                "metadatas": [{k: v for k, v in r.items() if k != "Comment"} for r in self.rows]}

    def as_retriever(self, search_kwargs):
        """Return a synchronous retriever honoring equality and rating filters."""
        self.kwargs = search_kwargs
        def invoke(question):
            """Retrieve matching rows as LangChain Documents."""
            where = search_kwargs.get("filter", {})
            clauses = where.get("$and", [where])
            rows = self.rows
            for clause in clauses:
                for key, value in clause.items():
                    rows = [r for r in rows if r[key] >= value["$gte"]] if isinstance(value, dict) else [r for r in rows if r[key] == value]
            return [Document(page_content=r["Comment"], metadata={k: v for k,v in r.items() if k != "Comment"}) for r in rows[:search_kwargs["k"]]]
        return SimpleNamespace(invoke=invoke)

    def add_documents(self, docs, ids):
        """Record ingested rows without touching production storage."""
        self.rows.extend(dict(doc.metadata, Comment=doc.page_content) for doc in docs)


class FakeChain:
    """Emit deterministic chunks while checking judge order."""

    async def astream(self, values):
        """Stream a cited answer and mark generation completion."""
        yield "Excellent pepperoni "
        yield "[FB-014]."
        self.finished = True


class FakeEvaluator:
    """Track calls and persist only to temporary test locations."""

    def __init__(self, chain):
        """Set up an empty history and a closable fake client."""
        self.chain = chain
        self._history = []
        self.client = self

    async def close(self):
        """Provide the client shutdown interface."""

    def evaluate_single(self, **kwargs):
        """Require generation to finish before any judge execution."""
        assert self.chain.finished
        scores = {"faithfulness": 1.0, "answer_relevancy": .8,
                  "llm_context_precision_without_reference": .9, "context_utilization": float("nan")}
        self._history.append({"scores": scores})
        return scores

    def get_summary(self):
        """Return aggregate state after a scored request."""
        return {"faithfulness": {"mean": 1., "min": 1., "max": 1., "count": len(self._history)}} if self._history else {}

    def save_history(self, path):
        """Write the test history to a caller-provided temporary path."""
        Path(path).write_text(json.dumps(self._history))


class WebTests(unittest.TestCase):
    """Exercise the HTTP and SSE contracts through FastAPI's actual lifespan."""

    def setUp(self):
        """Inject a deterministic engine through the startup factory."""
        self.store = FakeStore()
        self.chain = FakeChain()
        self.evaluator = FakeEvaluator(self.chain)
        self.engine = SimpleNamespace(store=self.store,chain=self.chain,evaluator=self.evaluator)
        self.patch = patch('api.server.create_engine', return_value=self.engine)
        self.patch.start()
        self.history_dir = tempfile.TemporaryDirectory()
        self.history_patch = patch('config.CHAT_HISTORY_PATH', Path(self.history_dir.name) / 'chats.sqlite3')
        self.history_patch.start()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        """Close resources and restore production construction."""
        self.client.__exit__(None,None,None)
        self.patch.stop()
        self.history_patch.stop()
        self.history_dir.cleanup()

    def stream(self, **kwargs):
        """Decode the JSON SSE frames from a complete response."""
        response = self.client.post('/api/chat/stream',json={"question":"Best pepperoni?", **kwargs})
        self.assertEqual(response.status_code,200)
        return [(lines[0][7:],json.loads(lines[1][6:])) for frame in response.text.split('\n\n') if (lines:=frame.splitlines()) and lines[0].startswith('event:')]

    def test_stream_and_evaluation(self):
        """Citations precede tokens and scores follow completed generation."""
        frames = self.stream()
        self.assertEqual([name for name,_ in frames],['citations','token','token','metrics','done'])
        self.assertEqual(frames[0][1][0],REVIEW)
        scores = frames[-2][1]['scores']
        self.assertEqual(set(scores),{'faithfulness','answer_relevancy','context_precision','context_utilization'})
        self.assertIsNone(scores['context_utilization'])
        self.assertEqual(len(self.evaluator._history),1)

    def test_filters_and_no_results(self):
        """Combined metadata filters return no unrelated evidence."""
        frames = self.stream(filters={"franchise":"Georgies Pizza","location":"East Auckland","min_rating":4})
        self.assertEqual(frames[0][1],[])
        self.assertEqual(len(self.store.kwargs['filter']['$and']),3)
        self.assertEqual(frames[-2][1]['evaluation_status'],'no_context')
        self.assertEqual(self.evaluator._history,[])

    def test_evaluation_disabled(self):
        """The instant path avoids judge execution."""
        frames = self.stream(eval_enabled=False)
        self.assertIsNone(frames[-2][1]['scores'])
        self.assertEqual(self.evaluator._history,[])

    def test_validation(self):
        """Reject blank questions and invalid rating ranges."""
        for payload in ({"question":" "},{"question":"hi","filters":{"min_rating":6}}):
            self.assertEqual(self.client.post('/api/chat/stream',json=payload).status_code,422)

    def test_metadata_and_static(self):
        """Serve the app, assets, and indexed filter options."""
        self.assertIn('PizzaLover',self.client.get('/').text)
        self.assertEqual(self.client.get('/static/app.js').status_code,200)
        self.assertEqual(self.client.get('/api/metadata/options').json()['franchises'],['Sals'])

    def test_json_ingest_duplicate_and_invalid(self):
        """Accept new review IDs and reject duplicates and malformed batches."""
        new = dict(REVIEW,Feedback_ID='FB-051')
        self.assertEqual(self.client.post('/api/ingest',json=new).json(),{'added':1})
        self.assertEqual(self.client.post('/api/ingest',json=new).status_code,409)
        self.assertEqual(self.client.post('/api/ingest',json=[dict(new,Feedback_ID='FB-052'),dict(new,Rating=8)]).status_code,422)
        self.assertEqual(len(self.store.rows),2)

    def test_csv_ingest(self):
        """Parse a real multipart CSV upload."""
        csv = 'Feedback_ID,Franchise,Location,Rating,Comment\nFB-060,Italianos,Westcity,4,Great basil\n'
        response = self.client.post('/api/ingest',files={'file':('reviews.csv',csv,'text/csv')})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json(),{'added':1})

    def test_save_and_summary(self):
        """Persist web history and report session and golden aggregates."""
        self.stream()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'history.json'
            golden = Path(directory)/'golden.csv'
            golden.write_text('faithfulness,answer_correctness\n0.8,0.9\n1.0,0.7\n')
            with patch('config.EVALUATION_HISTORY_PATH',path),patch('config.GOLDEN_RESULTS_CSV',golden):
                result = self.client.post('/api/evaluation/save').json()
                self.assertEqual(result['count'],1)
                self.assertEqual(len(json.loads(path.read_text())),1)
                summary = self.client.get('/api/evaluation/summary').json()
                self.assertEqual(summary['session']['faithfulness']['count'],1)
                self.assertEqual(summary['golden']['faithfulness']['mean'],.9)

    def test_stream_failure_is_explicit(self):
        """An engine failure emits error then done, preserving the SSE contract."""
        async def broken(values):
            """Simulate an unavailable Ollama endpoint."""
            raise ConnectionError('offline')
            yield ''
        self.chain.astream = broken
        frames = self.stream()
        self.assertEqual([name for name,_ in frames],['citations','error','done'])

    def test_persistent_chat_and_sources(self):
        """Save queries, answers, evidence and metrics, then continue the same chat."""
        self.client.get('/api/profiles')
        response = self.client.post('/api/chat/stream', json={"question": "Best pepperoni?", "eval_enabled": False})
        chat_id = response.headers['X-Chat-ID']
        first = self.client.get(f'/api/chats/{chat_id}').json()['turns'][0]
        self.assertEqual(first['question'], 'Best pepperoni?')
        self.assertEqual(first['text'], 'Excellent pepperoni [FB-014].')
        self.assertEqual(first['citations'], [REVIEW])
        self.assertEqual(first['status'], 'complete')
        self.assertEqual(first['metrics']['evaluation_status'], 'disabled')
        self.assertEqual(first['filters']['franchise'], None)
        self.assertIsNone(first['error'])
        self.stream(question='And the service?', chat_id=chat_id)
        turns = self.client.get(f'/api/chats/{chat_id}').json()['turns']
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[1]['metrics']['scores']['faithfulness'], 1.)
        from src.chat_history import ChatHistory
        reopened = ChatHistory(Path(self.history_dir.name) / 'chats.sqlite3')
        profile = self.client.get('/api/profiles').json()['current']
        self.assertEqual(len(reopened.chat(profile['id'], chat_id)['turns']), 2)
        self.assertEqual(self.client.get('/api/chats').json()['chats'][0]['turn_count'], 2)

    def test_profile_scoping_and_switching(self):
        """Another profile cannot list, fetch, append to, or delete the owner's chats."""
        alice = self.client.post('/api/profiles', json={"name": "Alice"}).json()
        response = self.client.post('/api/chat/stream', json={"question": "Alice's question", "eval_enabled": False})
        chat_id = response.headers['X-Chat-ID']
        self.assertEqual(self.client.post('/api/profiles', json={"name": "Bob"}).status_code, 201)
        self.assertEqual(self.client.get('/api/chats').json()['chats'], [])
        self.assertEqual(self.client.get(f'/api/chats/{chat_id}').status_code, 404)
        self.assertEqual(self.client.delete(f'/api/chats/{chat_id}').status_code, 404)
        self.assertEqual(self.client.post('/api/chat/stream', json={"question": "Wrong profile", "chat_id": chat_id}).status_code, 404)
        self.assertEqual(self.client.get('/api/chats', headers={'X-Profile-ID': alice['id']}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/profiles/{alice['id']}/select").status_code, 200)
        self.assertEqual(len(self.client.get('/api/chats').json()['chats']), 1)
        self.assertEqual(self.client.delete(f'/api/chats/{chat_id}').status_code, 204)
        self.assertEqual(self.client.get(f'/api/chats/{chat_id}').status_code, 404)
        with app.state.chat_history.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM turns').fetchone()[0], 0)

    def test_profile_validation_and_cookie(self):
        """Profiles have bounded unique names and the browser remembers its selection."""
        self.assertEqual(self.client.get('/api/chats').status_code, 401)
        response = self.client.get('/api/profiles')
        guest = response.json()['current']
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertEqual(self.client.get('/api/profiles').json()['current'], guest)
        self.assertEqual(self.client.post('/api/profiles', json={'name': ' Alice '}).json()['name'], 'Alice')
        self.assertEqual(self.client.post('/api/profiles', json={'name': 'alice'}).status_code, 409)
        for name in ('', '   ', 'a' * 81):
            self.assertEqual(self.client.post('/api/profiles', json={'name': name}).status_code, 422)
        self.assertEqual(self.client.get('/api/chats/not-a-uuid').status_code, 422)

    def test_failed_and_interrupted_queries_remain_in_history(self):
        """Partial output and query filters survive errors and generator cancellation."""
        async def broken(values):
            yield 'Partial answer'
            raise ConnectionError('offline')
        self.chain.astream = broken
        self.stream(filters={'min_rating': 4})
        chat_id = self.client.get('/api/chats').json()['chats'][0]['id']
        saved = self.client.get(f'/api/chats/{chat_id}').json()['turns'][0]
        self.assertEqual(saved['status'], 'failed')
        self.assertEqual(saved['text'], 'Partial answer')
        self.assertEqual(saved['filters']['min_rating'], 4)
        self.assertTrue(saved['error'])

        async def cancelled(values):
            yield 'Interrupted answer'
            raise asyncio.CancelledError()
        self.chain.astream = cancelled
        self.client.post('/api/chat/stream', json={'question': 'Stopped question'})
        chats = self.client.get('/api/chats').json()['chats']
        saved = self.client.get(f"/api/chats/{chats[0]['id']}").json()['turns'][0]
        self.assertEqual(saved['status'], 'interrupted')
        self.assertEqual(saved['text'], 'Interrupted answer')

    def test_empty_context_turn_is_saved(self):
        self.stream(filters={'franchise': 'Missing'})
        chat_id = self.client.get('/api/chats').json()['chats'][0]['id']
        turn = self.client.get(f'/api/chats/{chat_id}').json()['turns'][0]
        self.assertEqual(turn['status'], 'complete')
        self.assertEqual(turn['citations'], [])
        self.assertEqual(turn['metrics']['evaluation_status'], 'no_context')

    def test_chain_input_compatibility(self):
        """Existing CLI Document inputs retain citation metadata and model config."""
        doc = Document(page_content='Great',metadata={k:v for k,v in REVIEW.items() if k != 'Comment'})
        self.assertIn('[FB-014 | Sals | Downtown | 5★]',format_reviews([doc]))
        prompt,model,chain = build_rag_chain()
        self.assertIn('strictly',prompt.messages[0].prompt.template)
        self.assertEqual(model.model,'llama3.2')
        get_retriever(3,self.store)
        self.assertEqual(self.store.kwargs,{'k':3})


if __name__ == '__main__':
    unittest.main()
