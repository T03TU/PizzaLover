"""Real persistent Chroma regression: reset, query, reopen and ingest again."""
import tempfile
import unittest
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

import test_webapp as web
from api.server import app
from src.vector_store import init_vector_store, get_retriever


class LocalEmbeddings(Embeddings):
    def __init__(self):
        self.queries = 0

    def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0] for text in texts]

    def embed_query(self, text):
        self.queries += 1
        return [1.0, 0.0, 0.0]


class PersistentResetTests(unittest.TestCase):
    setUp = web.WebTests.setUp
    tearDown = web.WebTests.tearDown

    def test_real_index_stays_empty_and_old_chat_cannot_retrieve_deleted_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'chroma'
            path.mkdir()
            # Prevent initial CSV seeding in this isolated store.
            (path / 'initialized').touch()
            embeddings = LocalEmbeddings()
            store = init_vector_store(db_path=path, collection_name='reset_test', embeddings=embeddings)
            rows = [dict(web.REVIEW, Feedback_ID=f'FB-OLD-{i}') for i in range(1002)]
            store.add_documents([Document(page_content=row['Comment'], metadata={k:v for k,v in row.items() if k != 'Comment'}) for row in rows], ids=[row['Feedback_ID'] for row in rows])
            app.state.engine.store = store
            before = self.client.post('/api/chat/stream', json={'question':'List known pizza places', 'eval_enabled':False})
            self.assertEqual(before.status_code, 200)
            chat_id = before.headers['X-Chat-ID']
            self.assertIn('FB-OLD-', before.text)
            result = self.client.post('/api/pipeline/reset', json={'confirmation':'CONFIRM'})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json(), {'removed':1002})
            self.assertEqual(store.get(include=[])['ids'], [])
            self.assertEqual(self.client.get('/api/metadata/options').json()['total_documents'], 0)
            query_count = embeddings.queries
            async def no_generation(values):
                raise AssertionError('Generation must not run without indexed evidence')
                yield ''
            app.state.engine.chain.astream = no_generation
            after = self.client.post('/api/chat/stream', json={'question':'What were the old pizza places?', 'chat_id':chat_id})
            self.assertEqual(after.status_code, 200)
            self.assertIn('knowledge bank is empty', after.text)
            self.assertIn('event: citations\ndata: []', after.text)
            self.assertNotIn('FB-OLD-', after.text)
            self.assertEqual(embeddings.queries, query_count)
            reopened = init_vector_store(db_path=path, collection_name='reset_test', embeddings=embeddings)
            self.assertEqual(reopened.get(include=[])['ids'], [])
            self.assertEqual(get_retriever(vector_store=reopened).invoke('Old pizza places'), [])
            reopened.add_documents([Document(page_content='New review', metadata={'Feedback_ID':'FB-NEW'})], ids=['FB-NEW'])
            self.assertEqual(reopened.get(include=[])['ids'], ['FB-NEW'])


if __name__ == '__main__':
    unittest.main()
