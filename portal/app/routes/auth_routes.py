from flask import Blueprint, Flask, render_template, redirect, request, url_for, flash, make_response, g, session
from app.services.auth_service import validate_password, authenticate_user, create_user, load_current_user, login_github_user, login_user, logout_user, oauth, AuthError
from app.services.signup_guard import issue_signup_form, valid_signup_form

auth_bp = Blueprint('auth', __name__)

@auth_bp.get('/login')
def login():
    return render_template('login.html')

@auth_bp.post('/login')
def post_login():
    form = request.form
    email = form.get('email')
    password = form.get('password')
    user = authenticate_user(email, password)
    login_user(user)
    return redirect(url_for("actions.index"))

@auth_bp.get('/login/github')
def login_github():
    scheme = "http" if request.remote_addr == '127.0.0.1' else "https"
    redirect_uri = url_for('auth.login_github_authorized', _external=True, _scheme=scheme)
    return oauth.github.authorize_redirect(redirect_uri)

@auth_bp.route('/login/github/authorized')
def login_github_authorized():
    token = oauth.github.authorize_access_token()
    if token is None or token.get('access_token') is None:
        return 'Access denied: reason={} error={}'.format(
            request.args['error_reason'],
            request.args['error_description']
        )

    login_github_user(token)
    return redirect(url_for('actions.index'))

@auth_bp.post('/logout')
def logout():
    logout_user()
    response = make_response("", 200)
    response.headers['HX-Redirect'] = '/'
    return response

@auth_bp.get('/signup')
def signup():
    response = make_response(render_template('signup.html', signup_token=issue_signup_form(session)))
    response.headers['Cache-Control'] = 'no-store'
    return response

@auth_bp.post('/signup')
def post_signup():
    form = request.form
    if not valid_signup_form(session, form):
        flash('Please try the signup form again. Wait a moment before submitting.', 'error')
        return redirect(url_for('auth.signup'))
    email = form.get('email')
    password = form.get('password')
    password_confirmation = form.get('password-confirm')
    if password != password_confirmation:
        flash('Invalid password', 'error')
        return redirect(url_for('auth.signup'))

    if not validate_password(password):
        flash('Invalid password', 'error')
        return redirect(url_for('auth.signup'))

    user = create_user(email, password)

    flash('Your account has been created. Welcome!', 'success')
    login_user(user)
    return redirect(url_for('actions.index'))

@auth_bp.before_app_request
def load_current():
    g.current_user = load_current_user()

@auth_bp.app_context_processor
def inject_globals():
    return dict(current_user=g.get('current_user', None))
