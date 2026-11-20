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


def test_flattened_vision_page_is_rejected():
    import pytest
    from pptnative.native_author import validated_vision
    page = {'number': 76, 'role': 'content', 'layout_logic': 'split', 'capacity': 'medium',
            'safe_text_areas': [], 'decoration_conflicts': '', 'semantic_neutrality': 'low',
            'text_fill_compatible': True}
    assert validated_vision([page], [76]) == [page]
    with pytest.raises(ValueError, match='JSON object'):
        validated_vision([page, 'number', 'role'], [76, 77, 78])


def test_broken_vision_catalog_is_not_reused():
    from pptnative.native_author import complete_vision
    items = [{'number': 1}, {'number': 2}]
    assert not complete_vision([{'number': 1}, 'number', 'role'], items)
    assert not complete_vision([{'number': 1}], items)
    assert complete_vision([{'number': 2}, {'number': 1}], items)
