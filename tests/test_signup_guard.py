import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('signup_guard_under_test', ROOT / 'portal/app/services/signup_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class SignupGuardTests(unittest.TestCase):
    def setUp(self):
        self.state = {}
        with patch.object(guard.time, 'time', return_value=100):
            self.token = guard.issue_signup_form(self.state)

    def check(self, at=103, **fields):
        form = {'signup_token': self.token, **fields}
        with patch.object(guard.time, 'time', return_value=at):
            return guard.valid_signup_form(self.state, form)

    def test_normal_signup(self):
        self.assertTrue(self.check())
        self.assertNotIn(guard.SESSION_KEY, self.state)

    def test_honeypot(self):
        self.assertFalse(self.check(company_website='https://spam.invalid'))

    def test_fast_post(self):
        self.assertFalse(self.check(at=101))

    def test_expired_and_future_form(self):
        self.assertFalse(self.check(at=3701))
        self.setUp()
        self.assertFalse(self.check(at=99))

    def test_missing_and_forged_token(self):
        self.assertFalse(self.check(signup_token=''))
        self.setUp()
        self.assertFalse(self.check(signup_token='forged'))
        self.setUp()
        self.assertFalse(self.check(signup_token='☃'))

    def test_no_session_or_second_submission(self):
        self.assertTrue(self.check())
        self.assertFalse(self.check())


class SignupRouteTests(unittest.TestCase):
    def setUp(self):
        auth = types.ModuleType('app.services.auth_service')
        for name in ['validate_password', 'authenticate_user', 'create_user', 'load_current_user',
                     'login_github_user', 'login_user', 'logout_user', 'oauth']:
            setattr(auth, name, Mock())
        auth.AuthError = Exception
        auth.validate_password.return_value = True
        auth.load_current_user.return_value = None
        self.auth = auth
        spec = importlib.util.spec_from_file_location('auth_routes_under_test', ROOT / 'portal/app/routes/auth_routes.py')
        routes = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'app.services.auth_service': auth,
                                     'app.services.signup_guard': guard}):
            spec.loader.exec_module(routes)
        self.routes = routes
        self.app = Flask(__name__)
        self.app.secret_key = 'synthetic-test-only'
        self.app.config['TESTING'] = True
        self.app.register_blueprint(routes.auth_bp)
        self.app.add_url_rule('/', endpoint='actions.index', view_func=lambda: 'home')
        self.client = self.app.test_client()
        self.form = {'email': 'synthetic@example.invalid', 'password': 'example-password',
                     'password-confirm': 'example-password'}

    def get_form(self):
        with patch.object(self.routes, 'render_template', return_value='form'):
            response = self.client.get('/signup')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        with self.client.session_transaction() as session:
            token = session[guard.SESSION_KEY]['token']
            session[guard.SESSION_KEY] = {'token': token, 'issued': guard.time.time() - 3}
        return token

    def test_direct_post_cannot_create_or_login(self):
        self.assertEqual(self.client.post('/signup', data=self.form).status_code, 302)
        self.auth.create_user.assert_not_called()
        self.auth.login_user.assert_not_called()

    def test_form_success_and_duplicate_post(self):
        self.form['signup_token'] = self.get_form()
        self.assertEqual(self.client.post('/signup', data=self.form).location, '/')
        self.auth.create_user.assert_called_once_with('synthetic@example.invalid', 'example-password')
        self.client.post('/signup', data=self.form)
        self.auth.create_user.assert_called_once()

    def test_honeypot_and_cross_browser_rejected(self):
        self.form['signup_token'] = self.get_form()
        other = self.app.test_client()
        other.post('/signup', data=self.form)
        self.auth.create_user.assert_not_called()
        self.client.post('/signup', data={**self.form, 'company_website': 'spam'})
        self.auth.create_user.assert_not_called()

    def test_password_retry_gets_fresh_form(self):
        self.form['signup_token'] = self.get_form()
        response = self.client.post('/signup', data={**self.form, 'password-confirm': 'different'})
        self.assertEqual(response.location, '/signup')
        self.auth.create_user.assert_not_called()
        self.form['signup_token'] = self.get_form()
        self.client.post('/signup', data=self.form)
        self.auth.create_user.assert_called_once()


if __name__ == '__main__':
    unittest.main()
