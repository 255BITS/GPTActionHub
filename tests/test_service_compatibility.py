"""Exercise installed service entry points without DB, account or API access.

Run separately for each service with SERVICE=<directory> pytest -q tests.
"""
import asyncio
import importlib
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = os.environ.get('SERVICE', 'portal')
sys.path[:0] = [str(ROOT / SERVICE), str(ROOT)]

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Unmocked network access in offline service test')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.delenv('SLACK_WEBHOOK_URL', raising=False)
    monkeypatch.setenv('AWS_EC2_METADATA_DISABLED', 'true')


def test_real_service_routes(monkeypatch):
    if SERVICE == 'portal':
        db = Mock()
        db.count_apis.return_value = 0
        monkeypatch.setitem(sys.modules, 'shared.couch', SimpleNamespace(db=db, logs_db=Mock()))
        monkeypatch.setenv('FLASK_SECRET', 'offline-test-secret')
        monkeypatch.setenv('GITHUB_CLIENT_ID', 'offline-client')
        module = importlib.import_module('main')
        client = module.app.test_client()
        assert client.get('/login').status_code == 200
        from app.services import auth_service
        stored = {'_id': 'fixture-user', 'password_hash': auth_service.bcrypt.hash('fixture-password')}
        db.get_user_by_email.return_value = stored
        assert auth_service.authenticate_user('fixture@example.invalid', 'fixture-password') == stored
        with pytest.raises(auth_service.InvalidPasswordError):
            auth_service.authenticate_user('fixture@example.invalid', 'wrong')
        assert client.get('/').status_code == 200
        response = client.get('/login/github')
        assert response.status_code == 302
        assert response.location.startswith('https://github.com/login/oauth/authorize?')
        with client.session_transaction() as session:
            session['user_id'] = 'test-user'
        assert client.post('/logout').status_code == 200
        with client.session_transaction() as session:
            assert 'user_id' not in session
        # Run the existing OpenAPI fixture regressions against this real import.
        existing = importlib.import_module('portal.tests.unit.test_swagger_service')
        for name in dir(existing):
            if name.startswith('test_'):
                getattr(existing, name)()
    elif SERVICE == 'memory':
        module = importlib.import_module('app')
        monkeypatch.setattr(module.memorydb, 'write', Mock())
        monkeypatch.setattr(module.memorydb, 'read', Mock(return_value={'message': 'saved'}))
        client = module.app.test_client()
        assert client.get('/').status_code == 200
        assert client.post('/conversation/write', json={'message': 'hello'}).status_code == 204
        assert client.get('/conversation/read').json == {'message': 'saved'}
        assert client.get('/invalid/read').status_code == 400
    elif SERVICE == 'stability':
        module = importlib.import_module('stability')
        monkeypatch.setattr(module.requests, 'post', Mock(return_value=SimpleNamespace(status_code=200, json=lambda: {'artifacts': [{'base64': 'aW1hZ2U='}]})))
        monkeypatch.setattr(module, 'upload_to_s3', Mock(return_value='fixture.png'))
        monkeypatch.setattr(module, 'get_s3', Mock(return_value=SimpleNamespace(generate_presigned_url=Mock(return_value='https://example.invalid/image'))))
        client = module.app.test_client()
        assert client.get('/').status_code == 200
        response = client.post('/text-to-image', headers={'STABILITY_API_KEY': 'offline-key'}, json={'text': 'fixture'})
        assert response.status_code == 200
        assert 'https://example.invalid/image' in response.text
        assert module.requests.post.call_args.kwargs['json']['width'] == 640
    else:
        asyncio.run(check_async_service(monkeypatch))


async def check_async_service(monkeypatch):
    if SERVICE == 'together':
        import together
        model = {'name': 'offline-model', 'display_name': 'Fixture', 'display_type': 'language', 'description': 'fixture', 'config': {'prompt_format': '{prompt}', 'stop': ['END']}}
        monkeypatch.setattr(together.Models, 'list', Mock(return_value=[model]))
        monkeypatch.setattr(together.Complete, 'create', Mock(return_value={'output': {'choices': [{'text': 'done'}]}}))
        module = importlib.import_module('together_app')
    else:
        module = importlib.import_module(SERVICE)
    client = module.app.test_client()
    assert (await client.get('/')).status_code == 200
    if SERVICE == 'persona':
        monkeypatch.setattr(module, 'post_persona', AsyncMock(return_value={'id': 'fixture'}))
        monkeypatch.setattr(module, 'get_persona_status', AsyncMock(return_value={'status': 'completed', 'image_url': 'https://example.invalid/persona'}))
        response = await client.post('/profile', json={'prompt': 'fixture', 'happy': '0.5'})
        assert await response.get_data(as_text=True) == 'https://example.invalid/persona'
        module.post_persona.assert_awaited_once_with('fixture', 'dev', 0.5, 0.0)
    elif SERVICE == 'zazzle':
        entry = SimpleNamespace(title='Gift', media_content=[{'url': 'https://example.invalid/image'}], description='<p>Safe text</p>', id='https://example.invalid/gift', price='$1')
        monkeypatch.setattr(module.feedparser, 'parse', Mock(return_value=SimpleNamespace(entries=[entry])))
        response = await client.get('/search?q=fixture')
        assert response.status_code == 200
        assert 'Gift, Safe text' in await response.get_data(as_text=True)
    elif SERVICE == 'together':
        assert (await client.get('/model_list')).status_code == 200
        response = await client.post('/completion', json={'prompt': 'hello', 'model_name': 'offline-model'})
        assert response.status_code == 200
        assert 'Response: done' in await response.get_data(as_text=True)
        together.Complete.create.assert_called_once_with(prompt='hello', model='offline-model', max_tokens=256, stop=['END'])
    elif SERVICE == 'proxy':
        import base64
        from passlib.hash import bcrypt
        auth = {'auth_type': 'api_key_basic', 'username': 'CaseSensitive', 'password_hash': bcrypt.hash('PaSsWord')}
        target_path = {'path_id': 'path', 'url': 'https://example.invalid/upstream', 'method': 'POST', 'params': [{'name': 'X-Api-Key', 'type': 'credential'}]}
        module.action_lookup_table['action'] = {'auths': [auth], 'api_links': [{'api_id': 'api', 'paths': [{'operation_id': 'operation', 'path_id': 'path', 'params': [{'name': 'X-Api-Key', 'value': 'private-upstream-secret'}]}]}]}
        module.url_api_lookup_table['api'] = {'paths': [target_path]}
        upstream = SimpleNamespace(text='upstream result', status_code=200, headers={})
        transport = Mock(request=AsyncMock(return_value=upstream))
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=transport)
        context.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(module.httpx, 'AsyncClient', Mock(return_value=context))
        monkeypatch.setattr(module, 'async_log', AsyncMock())
        for credential in ['', 'Basic invalid!!', 'Basic ' + base64.b64encode(b'CaseSensitive:wrong').decode(), 'Basic ' + base64.b64encode(b'casesensitive:PaSsWord').decode()]:
            response = await client.post('/action/operation', headers={'Authorization': credential}, json={})
            assert response.status_code == 401
            assert 'Missing or invalid Authorization' in await response.get_data(as_text=True)
            transport.request.assert_not_awaited()
        token = base64.b64encode(b'CaseSensitive:PaSsWord').decode()
        response = await client.post('/action/operation', headers={'Authorization': 'bAsIc ' + token}, json={})
        assert response.status_code == 200
        assert 'upstream result' in await response.get_data(as_text=True)
        await asyncio.gather(*module.logging_tasks)
        transport.request.assert_awaited_once()
        assert transport.request.call_args.kwargs['headers']['X-Api-Key'] == 'private-upstream-secret'
        logged = module.async_log.call_args.args[0]['headers']
        assert 'Authorization' not in logged
        assert 'Authorization' not in transport.request.call_args.kwargs['headers']
        assert logged['X-Api-Key'] == '[REDACTED]'
        assert not module.validate_auth(base64.b64encode(b'CaseSensitive:PaSsWord').decode(), {'auth_type': 'unknown'})


def test_portal_requires_private_secret(monkeypatch):
    if SERVICE != 'portal':
        pytest.skip('portal only')
    monkeypatch.setitem(sys.modules, 'shared.couch', SimpleNamespace(db=Mock(), logs_db=Mock()))
    module = importlib.import_module('app')
    for bad_secret in [None, '', 'development']:
        if bad_secret is None:
            monkeypatch.delenv('FLASK_SECRET', raising=False)
        else:
            monkeypatch.setenv('FLASK_SECRET', bad_secret)
        with pytest.raises(RuntimeError, match='FLASK_SECRET'):
            module.create_app()
