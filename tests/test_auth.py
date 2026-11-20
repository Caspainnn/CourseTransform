import json


def _login(client, email):
    sent = client.post('/api/auth/code', json={'email': email})
    assert sent.status_code == 200
    code = sent.get_json().get('devCode')
    assert code
    done = client.post('/api/auth/login', json={'email': email, 'code': code})
    assert done.status_code == 200
    return done.get_json()


def _new_task(client):
    r = client.post('/api/tasks', json={'topic': '登录隔离测试', 'count': 6, 'style': 'academic'})
    assert r.status_code == 201
    return r.get_json()


def test_anonymous_requests_are_rejected(tmp_path, monkeypatch):
    from app import create_app
    monkeypatch.delenv('AUTH_DISABLED', raising=False)
    monkeypatch.setenv('AUTH_DEV_CODE', '1')
    client = create_app(tmp_path / 'data').test_client()
    assert client.get('/api/tasks').status_code == 401
    assert client.get('/api/auth/me').status_code == 401


def test_users_only_see_their_own_tasks(tmp_path, monkeypatch):
    from app import create_app
    monkeypatch.delenv('AUTH_DISABLED', raising=False)
    monkeypatch.setenv('AUTH_DEV_CODE', '1')
    app = create_app(tmp_path / 'data')
    alice, bob = app.test_client(), app.test_client()
    assert _login(alice, 'alice@example.com')['email'] == 'alice@example.com'
    task = _new_task(alice)
    assert [x['id'] for x in alice.get('/api/tasks').get_json()] == [task['id']]
    assert _login(bob, 'bob@example.com')['email'] == 'bob@example.com'
    assert bob.get('/api/tasks').get_json() == []
    assert bob.get('/api/tasks/' + task['id']).status_code == 404
    alice.post('/api/auth/logout')
    assert alice.get('/api/tasks').status_code == 401
