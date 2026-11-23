"""Recovery contracts use isolated account storage and mocked outbound delivery."""
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_webapp as web
from api.server import app
from src.access import Access
from src.chat_history import ChatHistory
from src.contacts import normalize_phone
from src.recovery import Recovery
from scripts.reset_users import reset_users

PASSWORD = 'new-account-password-123'


class RecoveryTests(unittest.TestCase):
    setUp = web.WebTests.setUp
    tearDown = web.WebTests.tearDown

    def add_contacts(self):
        user = self.client.get('/api/profiles').json()['current']
        result = self.client.patch(f"/api/users/{user['id']}", json={'email': ' ADMIN@Example.COM ', 'phone': '021 123 4567', 'country': 'NZ'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['phone'], '+64211234567')
        return user

    def request_code(self, channel='email', identifier='admin@example.com', country='NZ'):
        with patch('src.recovery.deliver_code') as deliver:
            response = self.client.post('/api/auth/recovery/request', json={'channel': channel, 'identifier': identifier, 'country': country})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['request_id'], deliver.call_args.args[2] if deliver.called else None

    def verify(self, request_id, code):
        return self.client.post('/api/auth/recovery/verify', json={'request_id': request_id, 'code': code})

    def complete(self, token, password=PASSWORD):
        return self.client.post('/api/auth/recovery/complete', json={'reset_token': token, 'password': password, 'confirm_password': password})

    def test_recovery_works_for_both_channels_and_revokes_sessions(self):
        user = self.add_contacts()
        for channel, identifier in [('email', ' ADMIN@EXAMPLE.COM '), ('phone', '021 123 4567')]:
            with self.subTest(channel=channel):
                old_session = self.client.cookies.get('pizzalover_session')
                request_id, code = self.request_code(channel, identifier)
                self.assertEqual(len(code), 6)
                verification = self.verify(request_id, code)
                self.assertEqual(verification.status_code, 200, verification.text)
                token = verification.json()['reset_token']
                self.assertEqual(self.verify(request_id, code).status_code, 400)
                self.assertEqual(self.complete(token).status_code, 200)
                self.assertEqual(self.complete(token).status_code, 400)
                self.assertIsNone(app.state.access.current(old_session))
                self.assertEqual(self.client.post('/api/auth/login', json={'name': identifier.strip(), 'password': PASSWORD, 'country': 'NZ'}).status_code, 200)
                self.assertEqual(self.client.get('/api/profiles').json()['current']['id'], user['id'])

    def test_unknown_and_disabled_accounts_receive_generic_response(self):
        self.add_contacts()
        known_id, _ = self.request_code()
        with patch('src.recovery.deliver_code') as deliver:
            known = self.client.post('/api/auth/recovery/request', json={'channel': 'email', 'identifier': 'admin@example.com'}).json()
            unknown = self.client.post('/api/auth/recovery/request', json={'channel': 'email', 'identifier': 'missing@example.com'}).json()
            self.assertEqual(deliver.call_count, 1)
        self.assertEqual(known['message'], unknown['message'])
        self.assertEqual(set(known), set(unknown))
        self.assertEqual(self.verify(unknown['request_id'], '000000').status_code, 400)
        self.assertEqual(self.verify(known_id, '000000').status_code, 400)
        user = self.client.post('/api/users', json={'name': 'Disabled', 'password': PASSWORD, 'email': 'disabled@example.com'}).json()
        self.client.patch(f"/api/users/{user['id']}", json={'active': False})
        _, code = self.request_code(identifier='disabled@example.com')
        self.assertIsNone(code)

    def test_expiry_resend_cross_channel_and_attempt_limit(self):
        self.add_contacts()
        old_id, old_code = self.request_code()
        new_id, new_code = self.request_code('phone', '+64211234567')
        self.assertEqual(self.verify(old_id, old_code).status_code, 400)
        with app.state.chat_history.connect() as db:
            db.execute("UPDATE password_resets SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (new_id,))
        self.assertEqual(self.verify(new_id, new_code).status_code, 400)
        request_id, code = self.request_code()
        wrong = '111111' if code != '111111' else '222222'
        for _ in range(5):
            self.assertEqual(self.verify(request_id, wrong).status_code, 400)
        self.assertEqual(self.verify(request_id, code).status_code, 400)

    def test_verified_grant_is_invalidated_by_resend_and_admin_change(self):
        user = self.add_contacts()
        request_id, code = self.request_code()
        token = self.verify(request_id, code).json()['reset_token']
        self.request_code('phone', '0211234567')
        self.assertEqual(self.complete(token).status_code, 400)
        request_id, code = self.request_code()
        token = self.verify(request_id, code).json()['reset_token']
        self.client.patch(f"/api/users/{user['id']}", json={'email': 'other@example.com'})
        self.assertEqual(self.complete(token).status_code, 400)

    def test_validation_password_match_unique_contacts_and_phone_aliases(self):
        self.add_contacts()
        self.assertEqual(normalize_phone('0211234567', 'NZ'), normalize_phone('+64 21 123 4567', 'US'))
        self.assertEqual(self.client.post('/api/users', json={'name': 'Duplicate', 'password': PASSWORD, 'phone': '0211234567', 'country': 'NZ'}).status_code, 409)
        self.assertEqual(self.client.post('/api/users', json={'name': 'Duplicate', 'password': PASSWORD, 'email': 'ADMIN@EXAMPLE.COM'}).status_code, 409)
        for identifier in ['123', 'bad']:
            self.assertEqual(self.client.post('/api/auth/recovery/request', json={'channel': 'phone', 'identifier': identifier}).status_code, 422)
        request_id, code = self.request_code()
        token = self.verify(request_id, code).json()['reset_token']
        self.assertEqual(self.complete(token, 'short').status_code, 422)
        self.assertEqual(self.client.post('/api/auth/recovery/complete', json={'reset_token': token, 'password': PASSWORD, 'confirm_password': PASSWORD + 'wrong'}).status_code, 422)
        self.assertEqual(self.complete(token).status_code, 200)

    def test_request_throttling_survives_restart_and_is_account_independent(self):
        for identifier in ['known@example.com', 'unknown@example.com']:
            for _ in range(5):
                self.request_code(identifier=identifier)
            response = self.client.post('/api/auth/recovery/request', json={'channel': 'email', 'identifier': identifier})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers['retry-after'], '600')
        recovery = Recovery(app.state.chat_history)
        from src.recovery import RateLimited
        with self.assertRaises(RateLimited):
            recovery.request('email', 'known@example.com', 'NZ', 'testclient')

    def test_console_delivery_and_country_options(self):
        self.add_contacts()
        with patch.dict('os.environ', {'SMTP_HOST': '', 'SMS_WEBHOOK_URL': ''}):
            options = self.client.get('/api/auth/recovery/options').json()
            self.assertEqual(options['delivery'], {'email': 'console', 'phone': 'console'})
            self.assertIn({'iso': 'NZ', 'dial_code': '+64'}, options['countries'])
            self.assertGreater(len(options['countries']), 200)
            with self.assertLogs('src.recovery', level='WARNING') as logs:
                response = self.client.post('/api/auth/recovery/request', json={'channel': 'phone', 'identifier': '0211234567', 'country': 'NZ'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('PASSWORD RESET', logs.output[0])
            self.assertIn('+64211234567', logs.output[0])
            self.assertNotIn('code', response.json())

    def test_provider_failure_does_not_disclose_account(self):
        self.add_contacts()
        with patch('src.recovery.deliver_code', side_effect=RuntimeError('offline')):
            with self.assertLogs('src.recovery', level='ERROR'):
                response = self.client.post('/api/auth/recovery/request', json={'channel': 'email', 'identifier': 'admin@example.com'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('offline', response.text)


class SeedTests(unittest.TestCase):
    def test_reset_clears_dependents_and_creates_usable_admin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'users.db'
            history = ChatHistory(path)
            access = Access(history)
            Recovery(history)
            user = access.setup(access.setup_token, 'Old', PASSWORD)
            access.session(user['id'])
            history.create_chat(user['id'], 'Old chat')
            result = reset_users(path)
            access = Access(history)
            self.assertEqual(result['removed_users'], 1)
            self.assertEqual(access.login('ADMIN', result['password'])['role'], 'ADMIN')
            self.assertEqual(access.login(result['email'], result['password'])['role'], 'ADMIN')
            self.assertEqual(access.login('(202) 555-0123', result['password'], 'US')['role'], 'ADMIN')
            with history.connect() as db:
                for table in ['sessions', 'chats', 'turns', 'password_resets', 'response_feedback', 'auth_rate_limits']:
                    self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT count(*) FROM profiles').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
