"""Synthetic unit packages only; these are never delivery/acceptance evidence."""
from pathlib import Path
import pytest
from PIL import Image
from pptx import Presentation
from pptx.util import Inches
from pptnative.models import save


def test_title_revision_rejects_body_change_before_promotion(tmp_path):
    from pptnative.scope import verify_revision_scope
    parent, candidate = tmp_path / 'versions/v001', tmp_path / 'versions/v002'
    for folder in (parent, candidate):
        (folder / 'preview').mkdir(parents=True)
        Image.new('RGB', (64, 36), 'white').save(folder / 'preview/deck_p1.png')
        Image.new('RGB', (64, 36), 'white').save(folder / 'preview/deck_p2.png')
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text = 'Original title'
    slide.shapes.add_textbox(Inches(1), Inches(3), Inches(5), Inches(1)).text = 'Original body'
    slide.notes_slide.notes_text_frame.text = 'Original notes'
    second = deck.slides.add_slide(deck.slide_layouts[6])
    second.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text = 'Untouched page'
    second.notes_slide.notes_text_frame.text = 'Untouched notes'
    deck.save(parent / 'deck.pptx')
    page = {'id': 'p01', 'title': 'Original title', 'point_ids': ['p01.f1'], 'asset_ids': [], 'notes': 'Original notes', 'origins': ['p01']}
    page2 = {**page, 'id': 'p02', 'title': 'Untouched page', 'point_ids': ['p02.f1'], 'notes': 'Untouched notes', 'origins': ['p02']}
    save(parent / 'plan.json', {'pages': [page, page2]})
    plan = {'pages': [{**page, 'title': 'Revised title'}, page2]}
    slide.shapes[0].text = 'Revised title'
    deck.save(candidate / 'deck.pptx')
    assert verify_revision_scope(tmp_path, candidate, plan, 'v001', {'kind': 'set_title', 'page_ids': ['p01']})['passed']
    slide.shapes[1].text = 'Body changed outside scope'
    deck.save(candidate / 'deck.pptx')
    with pytest.raises(ValueError, match='title-only'):
        verify_revision_scope(tmp_path, candidate, plan, 'v001', {'kind': 'set_title', 'page_ids': ['p01']})
    slide.shapes[1].text = 'Original body'
    second.notes_slide.notes_text_frame.text += ' unauthorized addition'
    deck.save(candidate / 'deck.pptx')
    with pytest.raises(ValueError, match='actual notes'):
        verify_revision_scope(tmp_path, candidate, plan, 'v001', {'kind': 'set_title', 'page_ids': ['p01']})
