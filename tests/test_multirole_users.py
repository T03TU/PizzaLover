"""Multiple-role authorization, usernames and preservation of existing accounts."""
import re
import tempfile
import unittest
from pathlib import Path

import test_webapp as web
from api.server import app
from src.access import Access
from src.chat_history import ChatHistory

PASSWORD = 'multirole-password-123'


class MultiRoleTests(unittest.TestCase):
    setUp = web.WebTests.setUp
    tearDown = web.WebTests.tearDown

    def make_role(self, name, permissions):
        self.assertEqual(self.client.post('/api/roles', json={'name': name, 'permissions': permissions}).status_code, 201)

    def make_user(self, username='Analyst123', roles=None):
        response = self.client.post('/api/users', json={'name': 'Display name ' + username, 'username': username,
            'password': PASSWORD, 'roles': roles or ['USER'], 'email': username+'@example.com'})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def login(self, identifier, identifier_type=None, country='NZ'):
        payload = {'name': identifier, 'password': PASSWORD, 'country': country}
        if identifier_type:
            payload['identifier_type'] = identifier_type
        return self.client.post('/api/auth/login', json=payload)

    def test_multiple_roles_combine_permissions_and_live_changes(self):
        self.make_role('CHAT', ['chat:use'])
        self.make_role('EVIDENCE', ['scorecards:view', 'feedback:submit'])
        user = self.make_user(roles=['CHAT', 'EVIDENCE'])
        self.assertEqual(user['roles'], ['CHAT','EVIDENCE'])
        self.assertEqual(set(user['permissions']), {'chat:use','scorecards:view','feedback:submit'})
        admin_session = self.client.cookies.get('pizzalover_session')
        self.assertEqual(self.login('aNaLySt123', 'account').status_code, 200)
        user_session = self.client.cookies.get('pizzalover_session')
        response = self.client.post('/api/chat/stream', json={'question':'Pizza?', 'eval_enabled':False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post('/api/auth/role',json={'role':'EVIDENCE'}).status_code,200)
        feedback = self.client.post('/api/feedback', json={'message_id':response.headers['X-Turn-ID'], 'rating':'thumbs_up'})
        self.assertEqual(feedback.json()['roles'], ['CHAT','EVIDENCE'])
        self.client.cookies.clear(); self.client.cookies.set('pizzalover_session', admin_session)
        self.assertEqual(self.client.delete('/api/roles/EVIDENCE').status_code, 422)
        response = self.client.patch(f"/api/users/{user['id']}", json={'roles':['CHAT']})
        self.assertEqual(response.status_code, 200, response.text)
        self.client.cookies.clear(); self.client.cookies.set('pizzalover_session', user_session)
        self.assertEqual(self.client.get('/api/evaluation/summary').status_code, 403)
        self.assertEqual(self.client.post('/api/feedback',json={}).status_code,403)
        self.assertEqual(self.client.get('/api/profiles').json()['current']['roles'], ['CHAT'])

    def test_multi_role_admin_cannot_be_locked_out_or_deleted(self):
        admin = self.client.get('/api/profiles').json()['current']
        response = self.client.patch(f"/api/users/{admin['id']}",json={'roles':['USER','ADMIN']})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(set(response.json()['roles']),{'USER','ADMIN'})
        self.assertEqual(self.client.patch(f"/api/users/{admin['id']}",json={'roles':['USER']}).status_code,422)
        self.assertEqual(self.client.patch(f"/api/users/{admin['id']}",json={'roles':[]}).status_code,422)
        self.assertEqual(self.client.patch(f"/api/users/{admin['id']}",json={'active':False}).status_code,422)
        self.assertEqual(self.client.delete(f"/api/users/{admin['id']}").status_code,403)
        self.assertEqual(set(self.client.get('/api/profiles').json()['current']['roles']),{'USER','ADMIN'})

    def test_role_updates_are_atomic_and_cannot_escalate(self):
        user = self.make_user()
        for roles in [['USER','missing'], ['USER','user']]:
            response = self.client.patch(f"/api/users/{user['id']}",json={'roles':roles,'username':'Changed123'})
            self.assertEqual(response.status_code,422,response.text)
            current = next(u for u in self.client.get('/api/users').json()['users'] if u['id']==user['id'])
            self.assertEqual(current['roles'],['USER'])
            self.assertEqual(current['username'],'Analyst123')
        self.assertEqual(self.client.patch(f"/api/users/{user['id']}",json={'roles':['USER'],'role':'ADMIN'}).status_code,422)
        self.make_role('INVITER',['users:create'])
        self.make_user('Inviter123',['INVITER'])
        self.login('Inviter123')
        self.assertEqual(self.client.post('/api/users',json={'name':'Escalated','password':PASSWORD,'roles':['USER','ADMIN']}).status_code,403)
        self.assertEqual(self.client.patch(f"/api/users/{user['id']}",json={'roles':['ADMIN']}).status_code,403)

    def test_username_is_unique_editable_and_distinct_from_contacts(self):
        user = self.make_user()
        for bad in ['has space','bad-name','has_underscore','email@example.com','+64211234567','éxample','']:
            response = self.client.post('/api/users',json={'name':'Invalid', 'username':bad,'password':PASSWORD})
            self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.client.post('/api/users',json={'name':'Other display','username':'analyst123','password':PASSWORD}).status_code,409)
        response = self.client.patch(f"/api/users/{user['id']}",json={'username':'NewAlias42'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.login('NewAlias42','account').status_code,200)
        self.assertEqual(self.login('Analyst123','account').status_code,401)
        self.assertEqual(self.login('Analyst123@example.com','account').status_code,200)
        self.assertEqual(self.login(user['name'],'account').status_code,401)

    def test_numeric_username_and_phone_can_be_selected_unambiguously(self):
        account = self.make_user('2025550123')
        phone_user = self.make_user('PhoneUser42')
        self.client.patch(f"/api/users/{phone_user['id']}",json={'phone':'+12025550123'})
        self.assertEqual(self.login('2025550123','account').json()['id'],account['id'])
        self.assertEqual(self.login('2025550123','phone','US').json()['id'],phone_user['id'])


class MultiRoleMigrationTests(unittest.TestCase):
    def test_username_collision_and_membership_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            history = ChatHistory(Path(directory)/'accounts.db')
            first = history.create_profile('Alice!')
            second = history.create_profile('Alice')
            third = history.create_profile('名字')
            access = Access(history)
            users = access.users()
            self.assertEqual(len({u['username'].casefold() for u in users}),3)
            self.assertTrue(all(re.fullmatch('[A-Za-z0-9]+',u['username']) for u in users))
            self.assertEqual(next(u for u in users if u['id']==first['id'])['roles'],['ADMIN'])
            access.setup(access.setup_token,'Ignored',PASSWORD)
            access.update_user(second['id'],{'roles':['USER','ADMIN'],'username':'Custom42','password':PASSWORD})
            before = access.users()
            access = Access(history)
            self.assertEqual(before,access.users())
            self.assertEqual(set(access.login('Custom42',PASSWORD)['roles']),{'USER','ADMIN'})
            self.assertEqual({u['id'] for u in users},{first['id'],second['id'],third['id']})


if __name__ == '__main__':
    unittest.main()
