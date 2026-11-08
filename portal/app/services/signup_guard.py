"""Small, dependency-free friction against automated signup form submissions.

This is not a CAPTCHA: determined bots can load the form and wait. No IP or
fingerprinting data is collected. The signed Flask session binds the random
form token and issue time to the browser that loaded the form.
"""
import secrets
import time

MIN_FORM_SECONDS = 2
MAX_FORM_SECONDS = 3600
SESSION_KEY = 'signup_form'


def issue_signup_form(session):
    token = secrets.token_urlsafe(32)
    session[SESSION_KEY] = {'token': token, 'issued': time.time()}
    return token


def valid_signup_form(session, form):
    state = session.pop(SESSION_KEY, None)
    if form.get('company_website', '').strip() or not isinstance(state, dict):
        return False
    token = form.get('signup_token', '')
    expected = state.get('token')
    issued = state.get('issued')
    if not isinstance(expected, str) or not isinstance(issued, (int, float)):
        return False
    age = time.time() - issued
    return bool(token and token.isascii() and secrets.compare_digest(token, expected)
                and MIN_FORM_SECONDS <= age <= MAX_FORM_SECONDS)
