import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pptnative.models import save
from pptnative.quality import render_fit_issues, semantic_coverage


class NativeQuality(unittest.TestCase):
    def test_model_cannot_waive_an_explicit_code_identifier(self):
        with TemporaryDirectory() as folder, patch('pptnative.quality.ask', return_value={
                'coverage':[{'fact_id':'f','status':'fully_visible','visible_evidence':['Evaluate plots'],'missing_information':''}]}):
            reviews, issues = semantic_coverage(Path(folder), {'native':True,'pages':[{'id':'p','point_ids':['f']}]},
                {'facts':{'f':'Call summarize_performance to evaluate plots'}}, [{'texts':['Evaluate plots']}])
            self.assertEqual(reviews[0]['coverage'][0]['status'], 'partially_visible')
            self.assertIn('summarize_performance', issues[0]['evidence'])

    def test_real_powerpoint_overflow_is_compared_with_source_baseline(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            version, source = root / 'v001', root / 'source'
            save(version / 'version.json', {'parent': None})
            obj = {'id': 7, 'text': 'old', 'frame': [10, 10, 200, 50],
                   'rendered_bounds': [10, 15, 180, 60]}
            save(source / 'text-metrics.json', {'slides': [{'page': 3, 'objects': [obj]}]})
            current = {**obj, 'text': 'new', 'rendered_bounds': [10, 15, 180, 60]}
            metrics = {'height': 720, 'slides': [{'page': 1, 'objects': [current]}]}
            save(version / 'preview/text-metrics.json', metrics)
            plan = {'native': True, 'pages': [{'id': 'p', 'edit_mode': 'template-fill', 'source_slide': 3}]}
            self.assertEqual(render_fit_issues(version, plan, source), [])
            current['rendered_bounds'][3] += 10
            save(version / 'preview/text-metrics.json', metrics)
            issues = render_fit_issues(version, plan, source)
            self.assertEqual(issues[0]['scope'], 'render-geometry')

    def test_visible_fact_proof_cannot_be_invented_or_taken_from_notes(self):
        with TemporaryDirectory() as folder:
            def api(*args, **kwargs):
                validate = kwargs['validate']
                bad = {'coverage': [{'fact_id': 'f', 'status': 'fully_visible',
                                     'visible_evidence': ['invented'], 'missing_information': ''}]}
                with self.assertRaisesRegex(ValueError, 'verbatim'):
                    validate(bad)
                duplicate = {'coverage': [{'fact_id': 'f', 'status': 'fully_visible',
                                          'visible_evidence': ['Actual visible text']}] * 2}
                with self.assertRaisesRegex(ValueError, 'exactly once'):
                    validate(duplicate)
                good = {'coverage': [{'fact_id': 'f', 'status': 'notes_only',
                                      'visible_evidence': [], 'missing_information': 'Key action absent'}]}
                validate(good)
                return good
            with patch('pptnative.quality.ask', side_effect=api):
                _, issues = semantic_coverage(Path(folder), {'native': True, 'pages': [
                    {'id': 'p', 'point_ids': ['f']}]}, {'facts': {'f': 'Required fact'}},
                    [{'texts': ['Actual visible text'], 'notes': 'Required fact'}])
            self.assertEqual(issues[0]['severity'], 'major')

    def test_malformed_xml_response_is_returned_to_api_for_repair(self):
        import json
        import xml.etree.ElementTree as ET
        from pptnative.models import ask
        with TemporaryDirectory() as folder, \
             patch('pptnative.models.settings', return_value={'LLM_MODEL': 'test'}), \
             patch('pptnative.models.time.sleep'), \
             patch('pptnative.models.call', side_effect=[
                 {'content': json.dumps({'svg': '<g>'})},
                 {'content': json.dumps({'svg': '<g/>'})}]):
            result = ask(Path(folder), 'svg', 'test', {}, validate=lambda d: ET.fromstring(d['svg']))
            self.assertEqual(result['svg'], '<g/>')
