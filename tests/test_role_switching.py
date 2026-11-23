"""Session-scoped role switching and enforcement of the selected role."""
import unittest
import test_webapp as web
from api.server import app


class RoleSwitchTests(unittest.TestCase):
    setUp = web.WebTests.setUp
    tearDown = web.WebTests.tearDown

    def assign_admin_roles(self, extra=None):
        user = self.client.get('/api/profiles').json()['current']
        result = self.client.patch(f"/api/users/{user['id']}", json={'roles':['ADMIN','USER'] + (extra or [])})
        self.assertEqual(result.status_code,200,result.text)
        return user

    def switch(self, role):
        return self.client.post('/api/auth/role',json={'role':role})

    def test_user_can_drop_admin_privileges_and_switch_back(self):
        self.assign_admin_roles()
        result=self.switch('user')
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['active_role'],'USER')
        self.assertNotIn('pipeline:manage',result.json()['permissions'])
        self.assertEqual(result.json()['username'],'Admin')
        self.assertEqual(self.client.get('/api/pipeline').status_code,403)
        self.assertEqual(self.client.get('/api/roles').status_code,403)
        self.assertEqual(self.client.get('/api/users').status_code,403)
        self.assertEqual(self.client.get('/api/evaluation/summary').status_code,200)
        self.assertEqual(self.client.get('/api/profiles').json()['current']['active_role'],'USER')
        self.assertEqual(self.switch('ADMIN').status_code,200)
        self.assertEqual(self.client.get('/api/pipeline').status_code,200)
        self.assertEqual(self.client.get('/api/users').status_code,200)

    def test_empty_permission_role_can_still_switch_and_unassigned_roles_are_rejected(self):
        self.client.post('/api/roles',json={'name':'EMPTY','permissions':[]})
        self.assign_admin_roles(['EMPTY'])
        self.assertEqual(self.switch('EMPTY').status_code,200)
        self.assertEqual(self.client.get('/api/profiles').json()['current']['permissions'],[])
        self.assertEqual(self.switch('missing').status_code,403)
        self.assertEqual(self.switch('ADMIN').status_code,200)
        user=self.client.post('/api/users',json={'name':'Reader','username':'Reader42','password':'reader-password-123'}).json()
        self.client.post('/api/auth/login',json={'name':'Reader42','password':'reader-password-123'})
        self.assertEqual(self.switch('ADMIN').status_code,403)
        self.assertEqual(self.client.get('/api/profiles').json()['current']['active_role'],'USER')

    def test_separate_sessions_keep_their_own_role_and_stale_tab_actions_fail(self):
        user=self.assign_admin_roles()
        other=app.state.access.session(user['id'])
        self.switch('USER')
        self.assertEqual(app.state.access.current(other)['active_role'],'ADMIN')
        self.assertEqual(self.client.get('/api/chats',headers={'X-Active-Role':'ADMIN'}).status_code,409)
        self.assertEqual(self.client.get('/api/chats',headers={'X-Active-Role':'USER'}).status_code,200)
        self.assertEqual(self.client.get('/api/profiles').json()['current']['active_role'],'USER')

    def test_removing_active_role_falls_back_to_an_assigned_role(self):
        user=self.assign_admin_roles()
        self.switch('USER')
        admin_session=app.state.access.session(user['id'])
        current_session=self.client.cookies.get('pizzalover_session')
        self.client.cookies.clear();self.client.cookies.set('pizzalover_session',admin_session)
        result=self.client.patch(f"/api/users/{user['id']}",json={'roles':['ADMIN']})
        self.assertEqual(result.status_code,200,result.text)
        self.client.cookies.clear();self.client.cookies.set('pizzalover_session',current_session)
        self.assertEqual(self.client.get('/api/profiles').json()['current']['active_role'],'ADMIN')
        self.assertEqual(self.switch('USER').status_code,403)

    def test_admin_only_actions_require_admin_to_be_selected(self):
        self.client.post('/api/roles',json={'name':'POWER','permissions':['users:create','roles:manage','pipeline:manage']})
        self.assign_admin_roles(['POWER'])
        target=self.client.post('/api/users',json={'name':'Target','password':'target-password-123'}).json()
        self.switch('POWER')
        self.assertEqual(self.client.post('/api/pipeline/reset',json={'confirmation':'CONFIRM'}).status_code,403)
        self.assertEqual(self.client.delete(f"/api/users/{target['id']}").status_code,403)
        self.assertEqual(len(self.store.rows),1)


if __name__=='__main__':
    unittest.main()
