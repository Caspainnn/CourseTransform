import io
import json
import zipfile
from PIL import Image


def test_source_image_inventory_survives_model_request_archival(monkeypatch, tmp_path):
    from app import create_app
    from pptnative import models
    app = create_app(tmp_path)
    client = app.test_client()
    image = io.BytesIO()
    Image.new('RGB', (20, 20), 'white').save(image, format='PNG')
    document = io.BytesIO()
    with zipfile.ZipFile(document, 'w') as z:
        z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Lesson facts</w:t></w:r></w:p></w:document>')
        z.writestr('word/media/image1.png', image.getvalue())
    uploaded = client.post('/api/files', data={'file': (io.BytesIO(document.getvalue()), 'lesson.docx')}).json
    task = client.post('/api/tasks', json={'topic': 'Course', 'count': 6, 'localRefs': [uploaded['fileId']]}).json
    monkeypatch.setattr(models, 'settings', lambda: {'LLM_MODEL': 'test', 'LLM_OUTLINE_JSON_MODEL': 'test-vision'})
    def transport(key, messages, **options):
        content = messages[1]['content']
        if isinstance(content, list):
            ids = json.loads(content[0]['text'])['asset_ids']
            data = {'assets': {ident: 'Real image description' for ident in ids}}
        else:
            data = {'pages': [{'id': f'p0{i}', 'title': f'Page {i}', 'points': ['Lesson facts'], 'assetIds': []} for i in range(1, 7)]}
        return {'content': json.dumps(data), '_finish_reason': 'stop'}
    monkeypatch.setattr(models, 'call', transport)
    app.extensions['product']['execute'](task['id'], 'outline', {})
    result = client.get('/api/tasks/'+task['id']).json
    assert result['status'] == 'outline_ready', result.get('error')
    assert len(result['outline']) == 6
