import xml.etree.ElementTree as ET
from pptnative import native_author, quality


def test_content_only_repair_preserves_all_image_geometry(monkeypatch, tmp_path):
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><text x="20" y="40" font-size="24">Title</text><text x="20" y="100" font-size="24">Full teaching fact</text><image href="../images/a.jpg" x="20" y="160" width="300" height="200"/></svg>'
    assets = {'a': {'workspace_href': '../images/a.jpg', 'width': 300, 'height': 200, 'minimum_display_width': 260}}
    def model(*args, **kwargs):
        result = {'edits': [{'index': 1, 'xml': '<text x="20" y="100" font-size="24">Full teaching fact, concise</text>'}], 'rationale': 'Text only', 'limitations': []}
        kwargs['validate'](result)
        return result
    monkeypatch.setattr(native_author, 'ask', model)
    # External PPT Master capacity checker is exercised by real authoring runs;
    # this portable unit checks that the edit boundary cannot move original images.
    monkeypatch.setattr(native_author, 'validate_text_capacity', lambda *a, **kw: None)
    monkeypatch.setattr(quality, 'semantic_coverage', lambda *a, **kw: ([], []))
    page = {'id': 'p01', 'title': 'Title', 'point_ids': ['f1'], 'notes': 'Original notes'}
    updated, result = native_author.repair_local_text(tmp_path, page, {'facts': {'f1': 'Full teaching fact'}},
                                                     svg, 'Original notes', [], assets)
    before = list(ET.fromstring(svg).iter('{http://www.w3.org/2000/svg}image'))[0]
    after = list(ET.fromstring(updated).iter('{http://www.w3.org/2000/svg}image'))[0]
    assert before.attrib == after.attrib
    assert 'concise' in updated
    assert result['notes'] == 'Original notes'
