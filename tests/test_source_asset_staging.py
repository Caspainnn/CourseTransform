from PIL import Image
import hashlib
from pptnative import native_author
from pptnative.models import save


def test_vector_only_source_can_stage_original_teaching_image(monkeypatch, tmp_path):
    job, version = tmp_path / 'job', tmp_path / 'job/versions/v001'
    workspace = version / 'workspace'
    source = workspace / 'authoring-svg-flat'
    source.mkdir(parents=True)
    save(source / 'authoring_summary.json', {})
    (source / 'slide_01.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><text x="20" y="40" font-size="24">Source</text></svg>', encoding='utf-8')
    original = tmp_path / 'original.png'
    Image.new('RGB', (20, 20), 'blue').save(original)
    page = {'id': 'p01', 'title': 'Title', 'point_ids': [], 'asset_ids': ['a1'], 'notes': ''}
    save(job / 'input/material-pool.json', {'facts': {}, 'assets': {'a1': {'path': str(original), 'sha256': hashlib.sha256(original.read_bytes()).hexdigest()}}, 'pages': [page]})
    monkeypatch.setattr(native_author, 'run_upstream', lambda *a, **kw: '')
    monkeypatch.setattr(native_author, 'visual_catalog', lambda *a: [{'number': 1, 'text_fill_compatible': True, 'capacity': 'medium'}])
    monkeypatch.setattr(native_author, 'ask', lambda *a, **kw: {'pages': [{'id': 'p01', 'source_slide': 1, 'rationale': 'Source'}]})
    native_author.prepare(job, version, {'pages': [page]}, tmp_path / 'source.pptx')
    assert (workspace / 'images/original.png').read_bytes() == original.read_bytes()


def test_style_source_uses_exact_1280_by_720_canvas(tmp_path):
    from app import style_template
    from pptx import Presentation
    source = tmp_path / 'style.pptx'
    style_template('academic', source)
    deck = Presentation(source)
    assert deck.slide_width == 1280 * 9525
    assert deck.slide_height == 720 * 9525


def test_outer_svg_format_error_reports_exact_source_canvas():
    import pytest
    base = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1279.96798 720"/>'
    with pytest.raises(ValueError, match='1279.96798'):
        native_author.content_nodes('<svg viewBox="0 0 1279.97 720"><text x="1" y="2">Text</text></svg>', base)
