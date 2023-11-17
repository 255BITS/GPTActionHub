from passlib.hash import bcrypt
from shared.couch import db
from flask import session, g, flash, redirect, url_for

GITHUB_CLIENT_SECRET = os.getenv('GITHUB_OAUTH_CONSUMER_SECRET', None)
GITHUB_CLIENT_ID = os.getenv('GITHUB_OAUTH_CONSUMER_KEY', None)

class AuthError(ValueError):
   def __init__(self, message, level='error'):
       self.message = message
       self.level = level

class InvalidPasswordError(AuthError):
    pass

class UserExistsError(AuthError):
    pass

class UserNotFoundError(AuthError):
    pass

class UnauthorizedError(AuthError):
    pass

def assert_logged_in():
    if g.current_user is None:
        raise UnauthorizedError("You must be logged in to access this page.", "warning")

def assert_owner(doc):
    if g.current_user is None:
        raise UnauthorizedError("You must be logged in to access this page.", "warning")
    if "user_id" not in doc or doc["user_id"] != g.current_user.id:
        raise UnauthorizedError("Document owner must match current user.", "warning")

def authenticate_user(email, password):
    user = db.get_user_by_email(email)
    if user is None:
        raise UserNotFoundError("This user does not exist.")
    if not bcrypt.verify(password, user["password_hash"]):
        raise InvalidPasswordError("Invalid Password.")
    return user

def create_user(email, password):
    if db.get_user_by_email(email):
        raise UserExistsError("This user already exists.")
    password_hash = bcrypt.hash(password)
    new_user = db.save({
        "password_hash": password_hash,
        "email": email,
        "verified": False,
        "type": "user"
    })
    return new_user

def load_current_user():
    if 'user_id' in session:
        return db.get(session['user_id'])
    return None

def login_github_user(token):
    resp = oauth.github.get('user')
    user_info = resp.json()
    user = db.get_user_by_email(user_info["email"])
    if user is None:
        user = db.save({
            "email": email,
            "verified": True,
            "type": "user",
            "profile_image" : user_info["avatar_url"],
            "github": user_info
        })

    session['user_id'] = user["_id"]

def login_user(user):
    session["user_id"] = user["_id"]

def logout_user():
    del session["user_id"]

def register_app_error_handlers(app):
    @app.errorhandler(AuthError)
    def handle_auth_error(error):
        flash(error.message, error.level)
        return redirect(url_for('auth.login'))

def setup_oauth(app):
    oauth.init_app(app)
        oauth.register(
            name='github',
            client_id=GITHUB_CLIENT_ID,
            client_secret=GITHUB_CLIENT_SECRET,
            access_token_url='https://github.com/login/oauth/access_token',
            access_token_params=None,
            authorize_url='https://github.com/login/oauth/authorize',
            authorize_params=None,
            api_base_url='https://api.github.com/',
            client_kwargs={'scope': 'user:email'}
        )

def validate_password(password):
    #TODO check for good enough password
    return True
