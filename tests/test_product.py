import io


def test_empty_store_upload_and_persistence(tmp_path):
    from app import create_app
    client = create_app(tmp_path).test_client()
    assert client.get('/api/knowledge').json == []
    assert client.get('/api/tasks').json == []
    result = client.post('/api/files', data={'file': (io.BytesIO('真实教材：机械传动'.encode()), 'lesson.txt')})
    assert result.status_code == 201
    file_id = result.json['fileId']
    task = client.post('/api/tasks', json={'topic': '机械传动', 'count': 7, 'localRefs': [file_id]}).json
    assert task['id']
    reopened = create_app(tmp_path).test_client().get('/api/tasks/' + task['id'])
    assert reopened.json['localRefs'] == [file_id]


def test_untrusted_uploads_and_paths(tmp_path):
    from app import create_app
    client = create_app(tmp_path).test_client()
    assert client.post('/api/files', data={'file': (io.BytesIO(b'fake'), 'bad.pptx'), 'kind': 'template'}).status_code == 400
    assert client.post('/api/files', data={'file': (io.BytesIO(b'MZ'), 'bad.exe')}).status_code == 400
    assert client.get('/api/tasks/../../.env').status_code == 404
    assert client.post('/api/tasks', json={'topic': 'x', 'localRefs': ['missing']}).status_code == 400


def test_stable_outline_identity_survives_intake(tmp_path):
    from app import validate_outline
    from pptnative.planning import intake
    pages = [{'id': 'pabcdef012345', 'title': '稳定页', 'points': ['事实一']},
             {'id': 'p000000000002', 'title': '第二页', 'points': []},
             {'id': 'p000000000003', 'title': '第三页', 'points': []}]
    validate_outline(pages)
    md = tmp_path / 'source.md'
    md.write_text('\n'.join(f'### {p["id"].upper()} {p["title"]}\n**上屏内容**\n- 事实一\n**页面关系**' for p in pages), encoding='utf-8')
    material = intake(md, tmp_path / 'input')
    assert [p['id'] for p in material['pages']] == [p['id'] for p in pages]


def test_empty_markdown_field_cannot_swallow_teacher_notes(tmp_path):
    from pptnative.planning import intake
    md = tmp_path / 'source.md'
    md.write_text('### P01 标题\n**上屏内容**\n- 事实\n**页面关系**\n\n**教师备注** 原始教师备注\n**本页目的** 讲解\n', encoding='utf-8')
    material = intake(md, tmp_path / 'input')
    assert material['pages'][0]['notes'] == '原始教师备注'


def test_general_revision_preserves_fact_and_asset_contract():
    from pptnative.planning import revise_plan
    from pathlib import Path
    page = {'id': 'p01', 'title': '标题', 'point_ids': ['f1'], 'asset_ids': ['a1'], 'notes': '原备注'}
    updated, changed = revise_plan({'title': '课题', 'pages': [page]},
        {'kind': 'edit_page', 'page_ids': ['p01'], 'instruction': '精简文字，保留全部核心事实'}, {}, Path('.'))
    assert changed == ['p01']
    assert updated['pages'][0] == page


def test_font_failure_maps_to_exact_original_text_slot():
    from pptnative.native_author import failure_text_indices
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text x="1" y="2" font-size="24">A</text><text x="1" y="3" font-size="24">B</text></svg>'
    assert failure_text_indices(svg, 'Template font adjustment exceeds the bounded source-relative range: slot 1 source font-size=24') == {1}


def test_stale_queued_operation_cannot_execute_after_cancel_and_resume(tmp_path):
    from app import create_app
    app = create_app(tmp_path)
    client = app.test_client()
    task = client.post('/api/tasks', json={'topic': '测试任务'}).json
    app.extensions['product']['execute'](task['id'], 'generate', {}, 'obsolete-operation-token')
    assert client.get('/api/tasks/' + task['id']).json['status'] == 'draft'
    assert not (tmp_path / 'tasks' / task['id'] / 'kernel').exists()


def test_single_page_request_cannot_target_a_different_page(monkeypatch, tmp_path):
    from pptnative import planning
    plan = {'pages': [{'id': 'p01', 'title': 'A'}, {'id': 'p02', 'title': 'B'}]}
    monkeypatch.setattr(planning, 'ask', lambda *a, **kw: {'kind': 'set_title', 'page_ids': ['p02'], 'new_title': 'C'})
    import pytest
    with pytest.raises(ValueError, match='selected page'):
        planning.recognize('change second page', 'v001', plan, tmp_path, allowed_page_ids=['p01'])


def test_real_template_layout_count_and_knowledge_filter(tmp_path):
    from app import create_app, style_template
    app = create_app(tmp_path / 'store')
    client = app.test_client()
    template = tmp_path / 'template.pptx'
    style_template('biz', template)
    from pptx import Presentation
    uploaded = client.post('/api/files', data={'kind': 'template', 'file': (io.BytesIO(template.read_bytes()), 'school.pptx')})
    assert uploaded.status_code == 201
    assert uploaded.json['layoutCount'] == len(Presentation(template).slide_layouts)
    ref = client.post('/api/files', data={'file': (io.BytesIO('教学目标与课堂核验'.encode()), 'teacher.txt')}).json
    entry = client.post('/api/knowledge', json={'fileId': ref['fileId'], 'type': 'jiaoan'}).json
    fresh = create_app(tmp_path / 'store').test_client()
    assert fresh.get('/api/knowledge?type=jiaocai').json == []
    assert fresh.get('/api/knowledge?q=课堂&type=jiaoan').json[0]['id'] == entry['id']


def test_scanned_pdf_and_cross_origin_rejected(tmp_path):
    from app import create_app
    from pypdf import PdfWriter
    client = create_app(tmp_path).test_client()
    pdf = PdfWriter()
    pdf.add_blank_page(width=600, height=800)
    data = io.BytesIO()
    pdf.write(data)
    result = client.post('/api/files', data={'file': (io.BytesIO(data.getvalue()), 'scan.pdf')})
    assert result.status_code == 400 and result.json['code'] == 'E_OCR_REQUIRED'
    assert client.post('/api/tasks', json={'topic': 'x'}, headers={'Origin': 'https://untrusted.example'}).status_code == 403


def test_potx_is_parsed_as_real_template_and_keeps_raw_sha(tmp_path):
    import zipfile
    import hashlib
    from app import create_app, style_template
    pptx = tmp_path / 'source.pptx'
    potx = tmp_path / 'source.potx'
    style_template('warm', pptx)
    with zipfile.ZipFile(pptx) as src, zipfile.ZipFile(potx, 'w') as dst:
        for info in src.infolist():
            content = src.read(info.filename)
            if info.filename == '[Content_Types].xml':
                content = content.replace(b'presentationml.presentation.main+xml', b'presentationml.template.main+xml')
            dst.writestr(info, content)
    client = create_app(tmp_path / 'data').test_client()
    r = client.post('/api/files', data={'kind': 'template', 'file': (io.BytesIO(potx.read_bytes()), 'school.potx')})
    assert r.status_code == 201
    assert r.json['slideCount'] == 4
    assert r.json['sha256'] == hashlib.sha256(potx.read_bytes()).hexdigest()


def test_model_regeneration_clears_old_approved_source(monkeypatch, tmp_path):
    import app as product
    from pptnative.models import read, save
    app = product.create_app(tmp_path)
    client = app.test_client()
    pages = [{'id': f'p0{i}', 'title': f'Page {i}', 'points': []} for i in range(1, 4)]
    task = client.post('/api/tasks', json={'topic': 'Course', 'outline': pages}).json
    path = tmp_path / 'tasks' / task['id'] / 'task.json'
    raw = read(path)
    raw['approvedOutlinePath'] = 'old-approved-source.md'
    save(path, raw)
    monkeypatch.setattr(product, 'ask', lambda *args, **kwargs: {'pages': [{**p, 'title': p['title']+' updated'} for p in pages]})
    app.extensions['product']['execute'](task['id'], 'outline', {'instruction': 'Update'})
    result = read(path)
    assert 'approvedOutlinePath' not in result
    assert result['outline'][0]['title'] == 'Page 1 updated'


def test_retry_existing_outline_preserves_regeneration_instruction(monkeypatch, tmp_path):
    import app as product
    import threading
    from pptnative.models import read, save
    app = product.create_app(tmp_path)
    client = app.test_client()
    pages = [{'id': f'p0{i}', 'title': f'Page {i}', 'points': []} for i in range(1, 4)]
    task = client.post('/api/tasks', json={'topic': 'Course', 'outline': pages}).json
    path = tmp_path / 'tasks' / task['id'] / 'task.json'
    raw = read(path)
    raw.update(status='failed', lastOperation='outline', lastPayload={'instruction': 'Keep the original evidence'})
    save(path, raw)
    observed, called = [], threading.Event()
    def model(*args, **kwargs):
        observed.append(args[3]['instruction'])
        called.set()
        raise RuntimeError('Intentional test boundary failure')
    monkeypatch.setattr(product, 'ask', model)
    assert client.post('/api/tasks/'+task['id']+'/resume', json={}).status_code == 202
    assert called.wait(3)
    assert observed == ['Keep the original evidence']


def test_resume_before_first_generation_initializes_real_style_template(monkeypatch, tmp_path):
    from app import create_app
    from pptnative import pipeline
    app = create_app(tmp_path)
    pages = [{'id': f'p0{i}', 'title': f'Page {i}', 'points': []} for i in range(1, 4)]
    task = app.test_client().post('/api/tasks', json={'topic': 'Course', 'outline': pages, 'style': 'mono'}).json
    found = []
    def create(kernel, template, outline):
        found.append(template.exists())
        return 'v001'
    monkeypatch.setattr(pipeline, 'create', create)
    app.extensions['product']['execute'](task['id'], 'resume', {})
    assert found == [True]


def test_outline_metadata_must_be_strings_before_it_can_be_saved():
    from app import validate_outline, Problem
    import pytest
    pages = [{'id': f'p0{i}', 'title': f'Page {i}', 'points': [], 'notes': ['invalid list']} for i in range(1, 4)]
    with pytest.raises(Problem, match='notes'):
        validate_outline(pages)
