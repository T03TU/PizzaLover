"""Authorization boundaries, migration, SQL isolation and durable response feedback."""
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import test_webapp as web
from api.server import app
from src.access import Access, PERMISSIONS, USER_PERMISSIONS
from src.chat_history import ChatHistory
from src.pipeline import Pipeline

PASSWORD = 'account-password-123'


class FeatureTests(unittest.TestCase):
    setUp = web.WebTests.setUp
    tearDown = web.WebTests.tearDown

    def create_user(self, role='USER', name='Reader'):
        response = self.client.post('/api/users', json={'name': name, 'password': PASSWORD, 'role': role})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def login(self, name='Reader', password=PASSWORD):
        self.client.cookies.clear()
        response = self.client.post('/api/auth/login', json={'name': name, 'password': password})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def ask(self):
        response = self.client.post('/api/chat/stream', json={'question': 'Pizza?', 'eval_enabled': False})
        self.assertEqual(response.status_code, 200, response.text)
        return response.headers['X-Chat-ID'], response.headers['X-Turn-ID']

    def test_admin_removes_lower_user_and_revokes_access(self):
        user = self.create_user()
        self.login()
        token = self.client.cookies.get('pizzalover_session')
        chat_id, turn_id = self.ask()
        self.client.post('/api/feedback', json={'message_id': turn_id, 'rating': 'thumbs_up'})
        self.login('Admin', 'admin-password-123')
        self.assertEqual(self.client.delete(f"/api/users/{user['id']}").status_code, 204)
        with app.state.chat_history.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM profiles WHERE id=?', (user['id'],)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM chats WHERE id=?', (chat_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM turns').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_feedback').fetchone()[0], 1)
        self.assertIsNone(app.state.access.current(token))
        self.assertEqual(self.client.post('/api/auth/login', json={'name': 'Reader', 'password': PASSWORD}).status_code, 401)

    def test_user_removal_rejects_self_peers_and_non_admin(self):
        actor = self.client.get('/api/profiles').json()['current']
        other = self.create_user('ADMIN')
        self.assertEqual(self.client.delete(f"/api/users/{actor['id']}").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/users/{other['id']}").status_code, 403)
        self.client.post('/api/roles', json={'name': 'FULL_ACCESS', 'permissions': list(PERMISSIONS)})
        equal = self.create_user('FULL_ACCESS', 'Equal')
        lower = self.create_user('USER', 'Lower')
        self.assertEqual(self.client.delete(f"/api/users/{equal['id']}").status_code, 403)
        self.login('Equal')
        self.assertEqual(self.client.delete(f"/api/users/{lower['id']}").status_code, 403)
        self.login('Lower')
        self.assertEqual(self.client.delete(f"/api/users/{other['id']}").status_code, 403)

    def test_admin_knowledge_reset_preserves_staging_and_chats(self):
        chat_id, turn_id = self.ask()
        self.client.post('/api/pipeline/stage', json=dict(web.REVIEW, Feedback_ID='FB-STAGED'))
        self.assertEqual(self.client.post('/api/pipeline/reset', json={'confirmation': 'wrong'}).status_code, 422)
        self.assertEqual(self.client.post('/api/pipeline/reset', json={'confirmation': 'RESET KNOWLEDGE BANK'}).status_code, 422)
        self.assertEqual(len(self.store.rows), 1)
        response = self.client.post('/api/pipeline/reset', json={'confirmation': 'CONFIRM'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'removed': 1})
        self.assertEqual(self.client.get('/api/metadata/options').json()['total_documents'], 0)
        self.assertEqual(self.client.get(f'/api/chats/{chat_id}').json()['turns'][0]['id'], turn_id)
        self.assertEqual(len(self.client.get('/api/pipeline').json()['rows']), 1)
        self.assertEqual(self.client.post('/api/pipeline/reset', json={'confirmation': 'CONFIRM'}).json(), {'removed': 0})
        self.assertEqual(self.client.post('/api/pipeline/embed').json(), {'added': 1})

    def test_reset_requires_admin_and_handles_storage_failure(self):
        self.client.post('/api/roles', json={'name': 'PIPELINE', 'permissions': ['pipeline:manage']})
        self.create_user('PIPELINE')
        self.login()
        self.assertEqual(self.client.post('/api/pipeline/reset', json={'confirmation': 'CONFIRM'}).status_code, 403)
        self.login('Admin', 'admin-password-123')
        def fail(ids):
            raise RuntimeError('Storage unavailable')
        self.store.delete = fail
        self.assertEqual(self.client.post('/api/pipeline/reset', json={'confirmation': 'CONFIRM'}).status_code, 503)

    def test_default_roles_and_api_denials(self):
        roles = self.client.get('/api/roles').json()
        self.assertEqual(set(roles['permissions']), set(PERMISSIONS))
        user = self.create_user()
        self.assertEqual(set(user['permissions']), set(USER_PERMISSIONS))
        self.login()
        for method, path, payload in [
            ('GET', '/api/pipeline', None), ('POST', '/api/pipeline/query', {'sql': 'SELECT 1'}),
            ('POST', '/api/pipeline/embed', None), ('POST', '/api/pipeline/stage', web.REVIEW),
            ('POST', '/api/ingest', web.REVIEW), ('POST', '/api/ingest/google', {}),
            ('POST', '/api/ingest/preview', {}), ('POST', '/api/users', {}),
            ('POST', '/api/profiles', {}), ('GET', '/api/users', None),
            ('GET', '/api/roles', None), ('POST', '/api/roles', {}),
            ('PUT', '/api/roles/ADMIN', {}), ('DELETE', '/api/roles/ADMIN', None),
            ('PATCH', f"/api/users/{user['id']}", {'role': 'ADMIN'})]:
            with self.subTest(path=path, method=method):
                self.assertEqual(self.client.request(method, path, json=payload).status_code, 403)
        self.assertEqual(self.client.get('/api/evaluation/summary').status_code, 200)
        self.ask()
        self.assertEqual(self.client.get('/api/profiles').json()['current']['role'], 'USER')

    def test_custom_roles_and_live_permission_revocation(self):
        response = self.client.post('/api/roles', json={'name': 'Chat analysts', 'permissions': ['chat:use']})
        self.assertEqual(response.status_code, 201, response.text)
        self.create_user(role='Chat analysts')
        admin_cookie = self.client.cookies.get('pizzalover_session')
        self.login()
        user_cookie = self.client.cookies.get('pizzalover_session')
        for path in ['/api/feedback', '/api/evaluation/summary']:
            self.assertEqual(self.client.request('POST' if path.endswith('feedback') else 'GET', path, json={}).status_code, 403)
        response = self.client.post('/api/chat/stream', json={'question': 'Pizza?', 'eval_enabled': True})
        self.assertEqual(response.status_code, 200)
        self.assertIn('"scores": null', response.text)
        self.assertEqual(self.evaluator._history, [])
        self.client.cookies.clear()
        self.client.cookies.set('pizzalover_session', admin_cookie)
        self.assertEqual(self.client.put('/api/roles/Chat analysts', json={'name': 'Chat analysts', 'permissions': []}).status_code, 200)
        self.client.cookies.clear()
        self.client.cookies.set('pizzalover_session', user_cookie)
        self.assertEqual(self.client.post('/api/chat/stream', json={'question': 'Pizza?'}).status_code, 403)
        self.assertEqual(self.client.get('/api/profiles').json()['current']['permissions'], [])

    def test_scores_are_redacted_from_saved_chats(self):
        user = self.create_user()
        self.login()
        response = self.client.post('/api/chat/stream', json={'question': 'Pizza?'})
        chat_id = response.headers['X-Chat-ID']
        self.assertIn('"faithfulness": 1.0', response.text)
        self.login('Admin', 'admin-password-123')
        self.client.post('/api/roles', json={'name': 'CHAT_ONLY', 'permissions': ['chat:use']})
        self.assertEqual(self.client.patch(f"/api/users/{user['id']}", json={'role': 'CHAT_ONLY'}).status_code, 200)
        self.login()
        turn = self.client.get(f'/api/chats/{chat_id}').json()['turns'][0]
        self.assertIsNone(turn['metrics']['scores'])

    def test_lockout_guard_and_role_lifecycle(self):
        admin = self.client.get('/api/profiles').json()['current']
        self.assertEqual(self.client.delete('/api/roles/admin').status_code, 422)
        self.assertEqual(self.client.put('/api/roles/admin', json={'name': 'admin', 'permissions': []}).status_code, 422)
        self.assertEqual(self.client.put('/api/roles/ADMIN', json={'name': 'ADMIN', 'permissions': ['chat:use']}).status_code, 422)
        for payload in [{'role': 'USER'}, {'active': False}]:
            self.assertEqual(self.client.patch(f"/api/users/{admin['id']}", json=payload).status_code, 422)
        self.assertEqual(set(self.client.get('/api/profiles').json()['current']['permissions']), set(PERMISSIONS))
        self.assertEqual(self.client.post('/api/roles', json={'name': 'CUSTOM', 'permissions': ['bad:permission']}).status_code, 422)
        self.assertEqual(self.client.post('/api/roles', json={'name': 'CUSTOM', 'permissions': []}).status_code, 201)
        other = self.create_user('CUSTOM')
        self.assertEqual(self.client.delete('/api/roles/CUSTOM').status_code, 422)
        self.client.patch(f"/api/users/{other['id']}", json={'role': 'ADMIN'})
        self.assertEqual(self.client.delete('/api/roles/CUSTOM').status_code, 204)
        self.assertEqual(self.client.patch(f"/api/users/{admin['id']}", json={'active': False}).status_code, 200)
        self.assertEqual(self.client.get('/api/profiles').status_code, 401)
        self.login()
        self.assertEqual(self.client.get('/api/profiles').json()['current']['role'], 'ADMIN')

    def test_user_creator_cannot_escalate_or_reset_admin(self):
        admin = self.client.get('/api/profiles').json()['current']
        self.client.post('/api/roles', json={'name': 'INVITER', 'permissions': ['users:create']})
        self.create_user('INVITER')
        self.login()
        self.assertEqual(self.client.post('/api/users', json={'name': 'New admin', 'password': PASSWORD, 'role': 'ADMIN'}).status_code, 403)
        self.assertEqual(self.client.patch(f"/api/users/{admin['id']}", json={'password': PASSWORD}).status_code, 403)
        self.assertEqual(self.client.post('/api/users', json={'name': 'New user', 'password': PASSWORD}).status_code, 201)

    def test_sessions_setup_and_forged_identity(self):
        admin = self.client.get('/api/profiles').json()['current']
        self.assertEqual(self.client.post('/api/auth/setup', json={'token': app.state.access.setup_token, 'name': 'Again', 'password': PASSWORD}).status_code, 403)
        self.assertEqual(self.client.post('/api/auth/login', json={'name': 'Admin', 'password': 'wrong'}).status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login', json={'name': 'Unknown', 'password': PASSWORD}).status_code, 401)
        self.assertEqual(self.client.post('/api/users', json={}, headers={'Origin': 'https://attacker.invalid'}).status_code, 403)
        self.client.post('/api/auth/logout')
        self.client.cookies.clear()
        self.client.cookies.set('pizzalover_session', admin['id'])
        self.assertEqual(self.client.get('/api/roles').status_code, 401)
        self.assertEqual(self.client.post('/api/chat/stream', json={'question': 'hi'}).status_code, 401)
        self.login('Admin', 'admin-password-123')
        old = self.client.cookies.get('pizzalover_session')
        self.client.patch(f"/api/users/{admin['id']}", json={'password': PASSWORD})
        self.client.cookies.clear()
        self.client.cookies.set('pizzalover_session', old)
        self.assertEqual(self.client.get('/api/profiles').status_code, 401)
        self.login('Admin')

    def test_password_whitespace_is_preserved(self):
        password = '  account password 123  '
        response = self.client.post('/api/users', json={'name': ' Spaced ', 'password': password})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['name'], 'Spaced')
        self.assertEqual(self.login('Spaced', password)['name'], 'Spaced')
        self.assertEqual(self.client.post('/api/auth/login', json={'name': 'Spaced', 'password': password.strip()}).status_code, 401)

    def test_staging_update_validate_embed_and_persistence(self):
        review = dict(web.REVIEW, Feedback_ID='FB-NEW')
        response = self.client.post('/api/pipeline/stage', json=review)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(len(self.store.rows), 1, 'staging must not invoke embeddings')
        self.assertEqual(self.client.post('/api/pipeline/stage', json=review).status_code, 409)
        response = self.client.post('/api/pipeline/query', json={'sql': "UPDATE staging_reviews SET Franchise = 'New name', Comment = 'Cleaned review'"})
        self.assertEqual(response.json()['affected_rows'], 1)
        self.assertEqual(Pipeline(app.state.pipeline.path).rows()[0]['Franchise'], 'New name')
        self.client.post('/api/pipeline/query', json={'sql': 'UPDATE staging_reviews SET Rating = 9'})
        self.assertEqual(self.client.post('/api/pipeline/embed').status_code, 422)
        self.assertEqual(len(self.store.rows), 1)
        self.client.post('/api/pipeline/query', json={'sql': 'UPDATE staging_reviews SET Rating = 4'})
        self.assertEqual(self.client.post('/api/pipeline/embed').json(), {'added': 1})
        self.assertEqual(self.store.rows[-1]['Comment'], 'Cleaned review')
        self.assertEqual(self.client.get('/api/pipeline').json()['rows'], [])
        self.assertEqual(self.client.post('/api/pipeline/embed').status_code, 422)
        self.client.post('/api/pipeline/stage', json=review)
        self.assertEqual(self.client.post('/api/pipeline/embed').status_code, 409)

    def test_sql_isolation_and_atomic_rejection(self):
        self.client.post('/api/pipeline/stage', json=dict(web.REVIEW, Feedback_ID='FB-NEW'))
        queries = [
            'DROP TABLE staging_reviews', 'ALTER TABLE staging_reviews ADD COLUMN evil TEXT',
            'CREATE TABLE evil (id TEXT)', 'DELETE FROM profiles', 'SELECT * FROM profiles',
            'SELECT * FROM sqlite_master', 'SELECT * FROM sqlite_schema',
            "ATTACH DATABASE '/tmp/not-allowed.db' AS extra", 'DETACH DATABASE main',
            'PRAGMA writable_schema = 1', 'VACUUM', 'BEGIN', 'COMMIT',
            "SELECT load_extension('/tmp/evil')", "SELECT writefile('/tmp/evil', 'oops')",
            "UPDATE staging_reviews SET Rating=1; DROP TABLE staging_reviews",
            "UPDATE staging_reviews SET Comment = (SELECT sql FROM sqlite_master LIMIT 1)",
            'CREATE TRIGGER bad AFTER INSERT ON staging_reviews BEGIN DELETE FROM staging_reviews; END',
            'SELECT randomblob(100000000)',
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                response = self.client.post('/api/pipeline/query', json={'sql': sql})
                self.assertEqual(response.status_code, 422, response.text)
        rows = self.client.get('/api/pipeline').json()['rows']
        self.assertEqual(rows[0][3], 5)
        result = self.client.post('/api/pipeline/query', json={'sql': 'SELECT count(*) AS total FROM staging_reviews'}).json()
        self.assertEqual(result['rows'], [[1]])
        result = self.client.post('/api/pipeline/query', json={'sql': "SELECT 1e999, x'ff'"}).json()
        self.assertEqual(result['rows'], [['inf', 'ff']])
        result = self.client.post('/api/pipeline/query', json={'sql': "UPDATE staging_reviews SET Franchise=upper(Franchise) RETURNING Franchise"}).json()
        self.assertEqual(result['affected_rows'], 1)
        self.assertEqual(result['rows'], [['SALS']])
        self.assertEqual(self.client.post('/api/pipeline/query', json={'sql': 'DELETE FROM staging_reviews'}).json()['affected_rows'], 1)

    def test_staged_ids_remain_unique_after_normalization(self):
        response = self.client.post('/api/pipeline/query', json={'sql': "INSERT INTO staging_reviews VALUES ('FB-SAME', 'Pizza', 'NZ', 5, 'Great'), ('FB-SAME ', 'Pizza', 'NZ', 5, 'Other')"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.post('/api/pipeline/embed').status_code, 422)
        self.assertEqual(len(self.store.rows), 1)

    def test_embedding_failure_retains_staged_rows(self):
        self.client.post('/api/pipeline/stage', json=dict(web.REVIEW, Feedback_ID='FB-NEW'))
        def fail(*args, **kwargs):
            raise RuntimeError('Embedding offline')
        self.store.add_documents = fail
        self.assertEqual(self.client.post('/api/pipeline/embed').status_code, 503)
        self.assertEqual(len(self.client.get('/api/pipeline').json()['rows']), 1)

    def test_feedback_metadata_ownership_completion_and_upsert(self):
        user = self.create_user()
        self.login()
        chat_id, message_id = self.ask()
        payload = {'message_id': message_id, 'rating': 'thumbs_up', 'comment': 'Helpful answer'}
        response = self.client.post('/api/feedback', json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        logged = response.json()
        self.assertEqual(logged['user_id'], user['id'])
        self.assertEqual(logged['role'], 'USER')
        self.assertTrue(logged['timestamp'])
        self.assertEqual(self.client.get(f'/api/chats/{chat_id}').json()['turns'][0]['feedback'], logged)
        self.client.post('/api/feedback', json=dict(payload, rating='thumbs_down', comment=''))
        with app.state.chat_history.connect() as db:
            rows = list(db.execute('SELECT * FROM response_feedback'))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['rating'], 'thumbs_down')
        for bad in [{'rating': 'five_stars'}, {'comment': 'x' * 4001}, {'role': 'ADMIN'}]:
            self.assertEqual(self.client.post('/api/feedback', json=dict(payload, **bad)).status_code, 422)
        incomplete = app.state.chat_history.start_turn(user['id'], chat_id, 'unfinished', {}, False)
        self.assertEqual(self.client.post('/api/feedback', json=dict(payload, message_id=incomplete['id'])).status_code, 422)
        self.assertEqual(self.client.post('/api/feedback', json=dict(payload, message_id=str(uuid4()))).status_code, 404)
        self.login('Admin', 'admin-password-123')
        self.assertEqual(self.client.post('/api/feedback', json=payload).status_code, 404)
        access = Access(ChatHistory(app.state.chat_history.path))
        with access.history.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_feedback').fetchone()[0], 1)


class MigrationTests(unittest.TestCase):
    def test_existing_users_and_chats_survive_idempotent_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            history = ChatHistory(Path(directory) / 'history.db')
            first = history.create_profile('First')
            other = history.create_profile('Other')
            chat = history.create_chat(other['id'], 'Keep my chat')
            access = Access(history)
            self.assertEqual([u['role'] for u in access.users()], ['ADMIN', 'USER'])
            access.setup(access.setup_token, 'Ignored name', PASSWORD)
            access.update_user(other['id'], {'password': PASSWORD})
            self.assertEqual(access.login('Other', PASSWORD)['role'], 'USER')
            self.assertEqual(history.chat(other['id'], chat['id'])['title'], 'Keep my chat')
            access.save_role('USER', ['chat:use'])
            access = Access(history)
            self.assertFalse(access.setup_required())
            self.assertEqual(access.login('First', PASSWORD)['id'], first['id'])
            self.assertEqual(access.login('Other', PASSWORD)['permissions'], ['chat:use'])


if __name__ == '__main__':
    unittest.main()
