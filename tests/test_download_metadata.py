"""Synthetic route fixture; does not download user files or bypass browser permission."""
import hashlib
from app import create_app, style_template
from pptnative.models import save


def test_head_reports_actual_version_hash_without_a_body(tmp_path):
    app = create_app(tmp_path)
    ident = 'a'*32
    folder = tmp_path / 'tasks' / ident / 'kernel/versions/v001'
    save(folder / 'version.json', {'id': 'v001', 'status': 'ready_for_review', 'qa_passed': True})
    style_template('mono', folder / 'deck.pptx')
    response = app.test_client().head(f'/api/tasks/{ident}/versions/v001/download')
    assert response.status_code == 200
    assert response.data == b''
    assert response.headers['X-Content-SHA256'] == hashlib.sha256((folder / 'deck.pptx').read_bytes()).hexdigest()
