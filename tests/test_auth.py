def _register(client, username, password='Passw0rd!'):
    r = client.post('/api/auth/register', json={'username': username, 'password': password})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _new_task(client):
    r = client.post('/api/tasks', json={'topic': '隔离测试', 'count': 6, 'style': 'academic'})
    assert r.status_code == 201
    return r.get_json()


def test_anonymous_requests_are_rejected(tmp_path, monkeypatch):
    from app import create_app
    monkeypatch.delenv('AUTH_DISABLED', raising=False)
    client = create_app(tmp_path / 'data').test_client()
    assert client.get('/api/tasks').status_code == 401
    assert client.get('/api/auth/me').status_code == 401


def test_password_login_isolates_tasks(tmp_path, monkeypatch):
    from app import create_app
    monkeypatch.delenv('AUTH_DISABLED', raising=False)
    app = create_app(tmp_path / 'data')
    alice, bob = app.test_client(), app.test_client()
    _register(alice, 'alice')
    task = _new_task(alice)
    assert [x['id'] for x in alice.get('/api/tasks').get_json()] == [task['id']]
    _register(bob, 'bob')
    assert bob.get('/api/tasks').get_json() == []
    assert bob.get('/api/tasks/' + task['id']).status_code == 404
    alice.post('/api/auth/logout')
    assert alice.get('/api/tasks').status_code == 401


def test_wrong_password_and_duplicate_names(tmp_path, monkeypatch):
    from app import create_app
    monkeypatch.delenv('AUTH_DISABLED', raising=False)
    app = create_app(tmp_path / 'data')
    client = app.test_client()
    _register(client, 'carol')
    client.post('/api/auth/logout')
    assert client.post('/api/auth/login', json={'username': 'carol', 'password': 'wrong-pass'}).status_code == 400
    assert client.post('/api/auth/register', json={'username': 'carol', 'password': 'Another1!'}).status_code == 400


def test_model_configs_are_per_user(tmp_path, monkeypatch):
    from app import create_app
    monkeypatch.delenv('AUTH_DISABLED', raising=False)
    app = create_app(tmp_path / 'data')
    alice, bob = app.test_client(), app.test_client()
    _register(alice, 'modela')
    _register(bob, 'modelb')
    r = alice.post('/api/models', json={'supplier': '自定义', 'baseUrl': 'https://llm.example.com/v1',
                                        'apiKey': 'sk-test', 'model': 'demo-model', 'role': 'reasoning',
                                        'features': {'vision': True}})
    assert r.status_code == 201
    body = r.get_json()
    assert body['hasKey'] and 'apiKey' not in body
    assert bob.get('/api/models').get_json()['items'] == []
    assert alice.get('/api/models').get_json()['active']['reasoning'] == body['id']
    assert alice.delete('/api/models/' + body['id']).status_code == 200
    assert alice.get('/api/models').get_json()['items'] == []
