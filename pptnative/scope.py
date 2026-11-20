"""Deterministic revision gates on actual exported packages and PowerPoint pixels."""
from copy import deepcopy
from lxml import etree
from PIL import Image, ImageChops
from pptx import Presentation
from .models import read, save


def canonical(node):
    return etree.tostring(node, method='c14n', exclusive=True)


def notes_xml(slide):
    return canonical(slide.notes_slide._element) if slide.has_notes_slide else None


def title_mask(slide, title):
    node = deepcopy(slide._element)
    matches = []
    for shape in node.xpath('.//p:sp'):
        if ''.join(''.join(shape.xpath('.//a:t/text()')).split()) == ''.join(title.split()):
            matches.append(shape)
    if len(matches) != 1:
        raise ValueError('title-only scope requires exactly one existing original title object')
    bodies = matches[0].xpath('./p:txBody')
    if len(bodies) != 1:
        raise ValueError('title-only title object has no text body')
    matches[0].remove(bodies[0])
    return canonical(node)


def verify_revision_scope(job, version, plan, parent_id, request):
    if not parent_id:
        return {'passed': True, 'initial': True}
    parent = job / 'versions' / parent_id
    old_plan = read(parent / 'plan.json')['pages']
    old = {p['id']: (i, p) for i, p in enumerate(old_plan)}
    before, after = Presentation(parent / 'deck.pptx'), Presentation(version / 'deck.pptx')
    unchanged = []
    for index, page in enumerate(plan['pages']):
        if page['id'] not in old or page['id'] in request['page_ids']:
            continue
        old_index, original = old[page['id']]
        for field in ('title', 'point_ids', 'asset_ids', 'notes', 'origins'):
            if page.get(field) != original.get(field):
                raise ValueError(f'Unrelated page contract changed: {page["id"]} {field}')
        if before.slides[old_index]._element.xml != after.slides[index]._element.xml:
            raise ValueError(f'Unrelated page XML changed: {page["id"]}')
        if notes_xml(before.slides[old_index]) != notes_xml(after.slides[index]):
            raise ValueError(f'Unrelated page actual notes changed: {page["id"]}')
        with Image.open(parent / f'preview/deck_p{old_index+1}.png') as a, Image.open(version / f'preview/deck_p{index+1}.png') as b:
            if a.size != b.size or ImageChops.difference(a.convert('RGB'), b.convert('RGB')).getbbox() is not None:
                raise ValueError(f'Unrelated page pixels changed: {page["id"]}')
        unchanged.append(page['id'])
    if request['kind'] == 'set_title':
        if [p['id'] for p in plan['pages']] != [p['id'] for p in old_plan]:
            raise ValueError('title-only revision changed page roster')
        for page_id in request['page_ids']:
            index, original = old[page_id]
            page = plan['pages'][index]
            a, b = before.slides[index], after.slides[index]
            if title_mask(a, original['title']) != title_mask(b, page['title']):
                raise ValueError('title-only revision changed non-title objects, geometry or slide structure')
            if notes_xml(a) != notes_xml(b):
                raise ValueError('title-only revision changed original speaker notes')
    report = {'passed': True, 'parent': parent_id, 'unchanged_xml_and_pixels': unchanged,
              'title_body_and_notes_unchanged': request['kind'] == 'set_title'}
    save(version / 'revision-scope-verification.json', report)
    return report
