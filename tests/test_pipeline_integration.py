"""Integration tests for the unified PDF→MD→lesson→PPT hand-off.

These exercise the Flask API and the real jiaoan offline engine; PPT native
production is covered by the existing stage-3 regression suite and the local
real run documented in README.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import pymupdf

from app import create_app
from pipeline import pdf2md

ROOT = Path(__file__).resolve().parents[1]
DEMO_PDF = ROOT / 'examples' / 'demo_textbook.pdf'
TEMPLATE = ROOT / 'examples' / 'templates' / '教案模板_表格式.docx'


def wait_job(client, url, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(url).get_json()
        if data['status'] not in ('queued', 'running'):
            return data
        time.sleep(0.4)
    raise AssertionError(f'timeout waiting for {url}')


class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='ct-test-'))
        self.app = create_app(self.tmp)
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pdf_job(self):
        with open(DEMO_PDF, 'rb') as handle:
            response = self.client.post('/api/pipeline/pdf/jobs',
                                        data={'file': (handle, DEMO_PDF.name)},
                                        content_type='multipart/form-data')
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        return wait_job(self.client, f"/api/pipeline/pdf/jobs/{response.get_json()['id']}")

    def test_split_chapters_ignores_toc_and_keeps_body(self):
        texts = [
            '封面\n第1章 概述 ⋯⋯⋯⋯ 1\n第2章 方法 ⋯⋯⋯⋯ 5\n',
            '第1章 概述\n1.1 背景\n正文内容。\n',
            '第2章 方法\n2.1 步骤\n正文内容。\n',
        ]
        chapters = pdf2md.split_chapters(texts)
        self.assertEqual([c['title'] for c in chapters], ['第1章 概述', '第2章 方法'])
        body = pdf2md.render_chapter(chapters[0], {})
        self.assertTrue(body.startswith('# 第1章 概述'))
        self.assertNotIn('⋯', body)
        self.assertIn('1.1 背景', body)

    def test_pdf_stage_extracts_markdown_and_artifact(self):
        job = self._pdf_job()
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertEqual(len(job['chapters']), 2)
        self.assertGreater(job['chapters'][0]['chars'], 200)
        self.assertGreaterEqual(job['result']['images'], 1)

        preview = self.client.get(f"/api/pipeline/pdf/jobs/{job['id']}/preview/1")
        self.assertEqual(preview.status_code, 200)
        text = preview.get_data(as_text=True)
        self.assertIn('# 第一章 走进人工智能', text)
        self.assertIn('1.1 什么是人工智能', text)

        artifacts = self.client.get('/api/pipeline/artifacts?stage=pdf2md').get_json()
        self.assertEqual(len(artifacts), 2)
        self.assertTrue((self.tmp / 'artifacts' / artifacts[0]['id'] / 'artifact.json').is_file())

        download = self.client.get(f"/api/pipeline/pdf/jobs/{job['id']}/download/1")
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.data.startswith('# 第一章'.encode('utf-8')))

    def test_scanned_pdf_reports_ocr_required(self):
        scanned = self.tmp / 'scanned.pdf'
        source = pymupdf.open(str(DEMO_PDF))
        pix = source[0].get_pixmap(dpi=90)
        target = pymupdf.open()
        page = target.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pix)
        target.save(scanned)
        target.close()
        source.close()

        with open(scanned, 'rb') as handle:
            response = self.client.post('/api/pipeline/pdf/jobs',
                                        data={'file': (handle, 'scanned.pdf')},
                                        content_type='multipart/form-data')
        self.assertEqual(response.status_code, 202)
        job = wait_job(self.client, f"/api/pipeline/pdf/jobs/{response.get_json()['id']}")
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['code'], 'E_OCR_REQUIRED')
        self.assertIn('MinerU', job['error'])
        self.assertFalse(self.client.get('/api/pipeline/artifacts?stage=pdf2md').get_json())

    def test_lesson_offline_generation_and_ppt_handoff(self):
        pdf = self._pdf_job()
        payload = {
            'subject': '信息科技', 'grade': '八年级', 'duration': 45,
            'lesson_type': '新授课', 'offline': True,
            'textbookArtifacts': [chapter['artifactId'] for chapter in pdf['chapters']],
        }
        with open(TEMPLATE, 'rb') as handle:
            response = self.client.post('/api/pipeline/lesson/jobs', content_type='multipart/form-data', data={
                'payload': json.dumps(payload, ensure_ascii=False),
                'templates': (handle, TEMPLATE.name),
            })
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        job = wait_job(self.client, f"/api/pipeline/lesson/jobs/{response.get_json()['id']}", timeout=300)
        self.assertIn(job['status'], ('succeeded', 'partial_failed'), job.get('error'))
        self.assertTrue(job['docx_files'])

        ok = next(item for item in job['docx_files'] if item['status'] in ('ok', 'ok_with_warnings'))
        docx = self.client.get(ok['url'])
        self.assertEqual(docx.status_code, 200)
        self.assertEqual(docx.data[:2], b'PK')

        artifact = self.client.post(f"/api/pipeline/lesson/jobs/{job['id']}/artifact/{ok['index']}")
        self.assertEqual(artifact.status_code, 201, artifact.get_data(as_text=True))
        artifact_id = artifact.get_json()['id']

        handoff = self.client.post('/api/pipeline/handoff/ppt', json={'artifactId': artifact_id})
        self.assertEqual(handoff.status_code, 200)
        data = handoff.get_json()
        self.assertGreaterEqual(len(data['refs']), 2)
        knowledge = self.client.get('/api/knowledge').get_json()
        self.assertTrue(any(item['type'] == 'jiaoan' for item in knowledge))
        self.assertTrue(all(item.get('origin') for item in knowledge))

        failed = [item for item in job['docx_files'] if item['status'] not in ('ok', 'ok_with_warnings')]
        if failed:
            blocked = self.client.post(f"/api/pipeline/lesson/jobs/{job['id']}/artifact/{failed[0]['index']}")
            self.assertEqual(blocked.status_code, 409)
            self.assertEqual(blocked.get_json()['code'], 'E_QUALITY_FAILED')

    def test_restart_marks_unfinished_jobs_interrupted(self):
        pdf_folder = self.tmp / 'pipeline' / 'pdf' / 'aaaaaaaaaaaa'
        pdf_folder.mkdir(parents=True)
        (pdf_folder / 'job.json').write_text(json.dumps({
            'id': 'aaaaaaaaaaaa', 'status': 'running', 'log': [], 'chapters': [],
            'fileName': 'x.pdf', 'sourcePath': str(pdf_folder / 'source.pdf'),
        }), encoding='utf-8')
        (pdf_folder / 'source.pdf').write_bytes(DEMO_PDF.read_bytes())

        lesson_folder = self.tmp / 'pipeline' / 'lesson' / 'bbbbbbbbbbbb'
        (lesson_folder / 'input').mkdir(parents=True)
        (lesson_folder / 'input' / 'textbook.md').write_text('# 第一章\n正文', encoding='utf-8')
        (lesson_folder / 'job.json').write_text(json.dumps({
            'id': 'bbbbbbbbbbbb', 'status': 'running', 'dir': str(lesson_folder),
            'out_dir': str(lesson_folder / 'out'), 'log': str(lesson_folder / 'run.log'),
            'chapters': 'all', 'results': [],
        }), encoding='utf-8')

        app = create_app(self.tmp)
        client = app.test_client()
        pdf_job = client.get('/api/pipeline/pdf/jobs/aaaaaaaaaaaa').get_json()
        lesson_job = client.get('/api/pipeline/lesson/jobs/bbbbbbbbbbbb').get_json()
        self.assertEqual(pdf_job['status'], 'interrupted')
        self.assertEqual(lesson_job['status'], 'interrupted')
        self.assertTrue(lesson_job['recoverable'])
        self.assertTrue(pdf_job['error'])


if __name__ == '__main__':
    unittest.main()