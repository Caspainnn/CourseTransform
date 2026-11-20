from PIL import Image


def test_unchanged_pages_keep_outstanding_content_findings():
    from pptnative.quality import unchanged_findings
    issue = {'page_id': 'p2', 'severity': 'minor', 'scope': 'content', 'evidence': 'uniform missing'}
    inherited = {'visual_coverage': {'p2': 'same', 'p7': 'old'}, 'issues': [issue, {**issue, 'page_id': 'p7'}]}
    assert unchanged_findings(inherited, {'p2': 'same', 'p7': 'new'}) == [issue]


def test_visual_cache_tracks_actual_pixels_and_content_not_source_number(tmp_path):
    from pptnative.quality import visual_fingerprint
    png = tmp_path / 'actual.png'
    Image.new('RGB', (64, 36), 'white').save(png)
    page = {'id': 'p01', 'title': 'Title', 'point_ids': ['f1'], 'asset_ids': [], 'notes': 'Notes', 'source_slide': 10}
    scene = {'engine': 'ppt-master-roundtrip', 'native_slide_sha256': 'same-xml', 'elements': []}
    first = visual_fingerprint(page, scene, png, {'tone': 'academic'})
    assert visual_fingerprint(page, scene, png, {'tone': 'academic'}, 'corrected actual notes') != first
    assert visual_fingerprint({**page, 'source_slide': 1}, scene, png, {'tone': 'academic'}) == first
    Image.new('RGB', (64, 36), 'black').save(png)
    assert visual_fingerprint(page, scene, png, {'tone': 'academic'}) != first
