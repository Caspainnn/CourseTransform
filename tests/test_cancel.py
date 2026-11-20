from pptnative.models import read, save


def _create(client):
    r = client.post('/api/tasks', json={'topic': '取消测试', 'count': 6, 'style': 'academic'})
    assert r.status_code == 201
    return r.get_json()['id']


def test_running_task_can_be_cancelled_and_draft_marked_failed(tmp_path):
    from app import create_app
    data = tmp_path / 'data'
    client = create_app(data).test_client()
    ident = _create(client)
    task_path = data / 'tasks' / ident / 'task.json'
    task = read(task_path)
    task.update(status='running', operation='generate', operationId='token-1')
    save(task_path, task)
    version = data / 'tasks' / ident / 'kernel/versions/v001'
    version.mkdir(parents=True)
    save(version / 'version.json', {'id': 'v001', 'parent': None, 'engine': 'ppt-master-roundtrip', 'status': 'building'})

    response = client.post(f'/api/tasks/{ident}/cancel')
    assert response.status_code == 200
    body = response.get_json()
    assert body['status'] == 'cancelling' and body['cancelRequested'] is True
    assert (data / 'tasks' / ident / 'kernel/cancel.requested').exists()
    assert read(version / 'version.json')['status'] == 'failed'


def test_queued_cancel_invalidates_worker_token(tmp_path):
    from app import create_app
    data = tmp_path / 'data'
    client = create_app(data).test_client()
    ident = _create(client)
    task_path = data / 'tasks' / ident / 'task.json'
    task = read(task_path)
    task.update(status='queued', operation='generate', operationId='token-2')
    save(task_path, task)

    body = client.post(f'/api/tasks/{ident}/cancel').get_json()
    assert body['status'] == 'cancelled'
    assert body['operationId'] != 'token-2'
    assert body['cancelRequested'] is True
