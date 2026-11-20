"""Executable branch and native template boundaries, independent of a template topic."""
import unittest
import xml.etree.ElementTree as ET
from pptnative.native_author import route_for, fresh_canvas, apply_text_edits, runtime_ids, content_nodes, retained_notes, validate_asset_references, body_capacity, merge_local_edits


class AuthorContracts(unittest.TestCase):
    def test_capacity_patch_cannot_rewrite_unrelated_slots(self):
        previous={'edits':[{'index':0,'xml':'Title'},{'index':1,'xml':'Body'}],'notes':'Keep notes'}
        with self.assertRaisesRegex(ValueError,'outside'):
            merge_local_edits(previous,{'patch_edits':[{'index':0,'xml':'Other'}]}, {1})
        result=merge_local_edits(previous,{'patch_edits':[{'index':1,'xml':'New body'}]}, {1})
        self.assertEqual(result['edits'][0],previous['edits'][0])
        self.assertEqual(result['notes'],'Keep notes')
    def test_changed_native_textbox_has_one_active_svg_fragment(self):
        source='<svg xmlns="http://www.w3.org/2000/svg"><g data-pptx-source-ref="slide:3" data-pptx-frame="0 0 300 100"><text x="10" y="25" font-size="20">First</text><text x="10" y="70" font-size="20">Last</text></g></svg>'
        with self.assertRaisesRegex(ValueError,'same native textbox'):
            apply_text_edits(source,[{'index':0,'xml':'<text x="10" y="25" font-size="20">New paragraph</text>'}])
        result=apply_text_edits(source,[{'index':0,'xml':'<text x="10" y="25" font-size="20"><tspan>New paragraph</tspan><tspan x="10" dy="25">Next line</tspan></text>'},{'index':1,'xml':''}])
        self.assertNotIn('Last',result)

    def test_native_capacity_ranks_real_paragraph_space_above_labels(self):
        def slot(width,height,bold=False):
            return {'text':'Placeholder body content','attributes':{'font-size':'20','font-weight':'bold' if bold else 'normal'},
                    'ancestors':[{'data-pptx-frame':f'0 0 {width} {height}'}]}
        self.assertGreater(body_capacity([slot(600,240)]), body_capacity([slot(180,80)]*4))
        self.assertEqual(body_capacity([slot(600,240,True)]), 0)
    def test_composition_cannot_drop_substitute_or_crop_fixed_assets(self):
        assets = {'a': {'workspace_href': '../images/a.jpg'}}
        valid = ET.fromstring('<g><image href="../images/a.jpg"/></g>')
        validate_asset_references([valid], assets)
        for fragment in ('<g/>', '<image href="wrong.jpg"/>',
                         '<image href="../images/a.jpg" preserveAspectRatio="xMidYMid slice"/>'):
            with self.assertRaises(ValueError):
                validate_asset_references([ET.fromstring(fragment)], assets)

    def test_readability_uses_actual_contain_width_instead_of_outer_box(self):
        assets = {'a': {'workspace_href': 'a.jpg', 'width': 1000, 'height': 500,
                        'minimum_display_width': 600}}
        # Nominal width 800 is misleading: the short box constrains width to 400.
        with self.assertRaisesRegex(ValueError, 'effective contain width=400'):
            validate_asset_references([ET.fromstring('<image href="a.jpg" width="800" height="200"/>')], assets)
        validate_asset_references([ET.fromstring('<image href="a.jpg" width="800" height="400"/>')], assets)

    def test_background_and_id_normalization_preserves_last_visible_background(self):
        root=ET.fromstring('<svg viewBox="0 0 100 50"><rect x="0" y="0" width="100" height="50" fill="#FFFFFF"/><rect x="0" y="0" width="100" height="50" fill="#123456"/><text x="4" y="20">text</text></svg>')
        runtime_ids(root)
        self.assertEqual(len(root),2)
        self.assertEqual(root[0].get('fill'),'#123456')
        self.assertTrue(all(n.get('id') for n in root))
        self.assertEqual(root[1].get('x'),'4')

    def test_full_canvas_api_wrapper_keeps_font_presentation(self):
        nodes=content_nodes('<svg viewBox="0 0 1280 720" font-family="Arial"><text x="1" y="2">T</text></svg>', '<svg viewBox="0 0 1280 720"/>')
        self.assertTrue(nodes[0].tag.endswith('g'))
        self.assertEqual(nodes[0].get('font-family'),'Arial')

    def test_fixed_note_retention_does_not_depend_on_model_copy_accuracy(self):
        note=retained_notes('Model prose',{'point_ids':['f'],'notes':'Original teacher note'},{'facts':{'f':'Exact fixed source fact'}})
        self.assertIn('Exact fixed source fact',note)
        self.assertIn('Original teacher note',note)

    def test_images_always_take_separate_composition_branch(self):
        self.assertEqual(route_for({"asset_ids": ["a"]}), "local-recompose")
        self.assertEqual(route_for({"asset_ids": []}), "template-fill")

    def test_fresh_canvas_removes_slide_art_but_keeps_inherited_objects(self):
        source = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect width="1280" height="720" fill="#fff"/><g data-pptx-source-ref="slide:3"><text>old</text></g><g data-pptx-source-ref="master:4"><path d="M0 0"/><text>brand</text></g></svg>'
        clean = fresh_canvas(source)
        self.assertNotIn('slide:3', clean)
        self.assertNotIn('old', clean)
        self.assertIn('master:4', clean)
        self.assertIn('brand', clean)

    def test_text_fill_cannot_move_group_or_add_background_to_hide_overflow(self):
        source = '<svg><g transform="translate(10 20)"><text x="5" y="9" font-size="24">old</text><rect width="200" height="50"/></g></svg>'
        result = apply_text_edits(source, [{"index": 0, "xml": '<text x="5" y="9" font-size="24">new</text>'}])
        self.assertIn('new', result)
        with self.assertRaises(ValueError):
            apply_text_edits(source, [{"index": 0, "xml": '<rect width="400" height="80"/><text x="5" y="9">new</text>'}])
        with self.assertRaises(ValueError):
            apply_text_edits(source, [{"index": 0, "xml": '<text x="105" y="9" font-size="24">new</text>'}])
        with self.assertRaises(ValueError):
            apply_text_edits(source, [{"index": 0, "xml": '<text x="5" y="9" font-size="24"><tspan font-size="2">new</tspan></text>'}])

    def test_source_mixed_font_hierarchy_is_preserved(self):
        source='<svg><text x="1" y="2" font-size="24"><tspan font-size="16">body</tspan></text></svg>'
        result=apply_text_edits(source,[{"index":0,"xml":'<text x="1" y="2" font-size="24"><tspan font-size="16">new</tspan></text>'}])
        self.assertIn('new',result)
