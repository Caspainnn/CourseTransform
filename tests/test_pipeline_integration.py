"""Integration tests for the unified PDF→MD→lesson→PPT hand-off.

These exercise the Flask API and the real jiaoan offline engine; PPT native
production is covered by the existing stage-3 regression suite and the local
real run documented in README.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import shutil
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

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

    def test_repeated_running_header_is_not_a_new_chapter(self):
        title = '第6章 贝叶斯和SVM分类算法的安全应用'
        texts = [f'{title}\n第一页正文。\n', f'{title}\n第二页正文。\n']
        chapters = pdf2md.split_chapters(texts)
        self.assertEqual(len(chapters), 1)
        markdown = pdf2md.render_chapter(chapters[0], {})
        self.assertEqual(markdown.count(title), 1)
        self.assertIn('第一页正文', markdown)
        self.assertIn('第二页正文', markdown)

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

        bundle = self.client.get(f"/api/pipeline/pdf/jobs/{job['id']}/bundle")
        self.assertEqual(bundle.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(bundle.data)) as archive:
            names = archive.namelist()
            self.assertEqual(len([name for name in names if name.startswith('markdown/') and name.endswith('.md')]), 2)
            self.assertTrue(any(name.startswith('images/') for name in names))
            self.assertIn('manifest.json', names)
            manifest = json.loads(archive.read('manifest.json').decode('utf-8'))
            self.assertEqual(manifest['fileName'], 'demo_textbook.pdf')
            self.assertEqual(len(manifest['chapters']), 2)

        download = self.client.get(f"/api/pipeline/pdf/jobs/{job['id']}/download/1")
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.data.startswith('# 第一章'.encode('utf-8')))

    def test_edit_chapter_creates_revision_and_serves_images(self):
        job = self._pdf_job()
        chapter = next(item for item in job['chapters'] if item['imageCount'])
        original_id = chapter['artifactId']
        edited = '# 第一章 走进人工智能\n\n人工修改后的正文。\n'

        response = self.client.post(
            f"/api/pipeline/pdf/jobs/{job['id']}/chapters/{chapter['index']}",
            json={'markdown': edited},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()
        updated = next(item for item in data['chapters'] if item['index'] == chapter['index'])
        self.assertEqual(updated['revision'], 2)
        self.assertTrue(updated['edited'])
        self.assertEqual(updated['chars'], len(edited))
        self.assertNotEqual(updated['artifactId'], original_id)

        preview = self.client.get(f"/api/pipeline/pdf/jobs/{job['id']}/preview/{chapter['index']}")
        self.assertEqual(preview.get_data(as_text=True).replace('\r\n', '\n'), edited)
        download = self.client.get(f"/api/pipeline/pdf/jobs/{job['id']}/download/{chapter['index']}")
        self.assertEqual(download.get_data(as_text=True).replace('\r\n', '\n'), edited)

        record = json.loads((self.tmp / 'artifacts' / updated['artifactId'] / 'artifact.json').read_text(encoding='utf-8'))
        self.assertTrue(record['meta']['edited'])
        self.assertEqual(record['meta']['revision'], 2)
        self.assertTrue((self.tmp / 'artifacts' / original_id / 'artifact.json').is_file())
        image = Path(record['files']['images'][0]).name
        served = self.client.get(f"/api/pipeline/pdf/jobs/{job['id']}/images/{chapter['index']}/{image}")
        self.assertEqual(served.status_code, 200)
        self.assertTrue(served.content_type.startswith('image/'))
        blocked = self.client.get(f"/api/pipeline/pdf/jobs/{job['id']}/images/{chapter['index']}/job.json")
        self.assertEqual(blocked.status_code, 404)

        empty = self.client.post(
            f"/api/pipeline/pdf/jobs/{job['id']}/chapters/{chapter['index']}",
            json={'markdown': '   '},
        )
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(empty.get_json()['code'], 'E_EMPTY_CONTENT')

    def test_scanned_pdf_reports_ocr_required_when_disabled(self):
        scanned = self.tmp / 'scanned.pdf'
        source = pymupdf.open(str(DEMO_PDF))
        pix = source[0].get_pixmap(dpi=90)
        target = pymupdf.open()
        page = target.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pix)
        target.save(scanned)
        target.close()
        source.close()

        with mock.patch.dict(os.environ, {'PDF_OCR': '0', 'PDF_OCR_ENGINE': 'rapidocr', 'MINERU_COMMAND': ''}):
            with open(scanned, 'rb') as handle:
                response = self.client.post('/api/pipeline/pdf/jobs',
                                            data={'file': (handle, 'scanned.pdf')},
                                            content_type='multipart/form-data')
            self.assertEqual(response.status_code, 202)
            job = wait_job(self.client, f"/api/pipeline/pdf/jobs/{response.get_json()['id']}")
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['code'], 'E_OCR_REQUIRED')
        self.assertIn('PDF_OCR=0', job['error'])
        self.assertFalse(self.client.get('/api/pipeline/artifacts?stage=pdf2md').get_json())

    def test_scanned_pdf_uses_local_ocr(self):
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except ImportError:
            self.skipTest('rapidocr_onnxruntime not installed')
        scanned = self.tmp / 'scanned_ocr.pdf'
        source = pymupdf.open(str(DEMO_PDF))
        pix = source[0].get_pixmap(dpi=150)
        target = pymupdf.open()
        page = target.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pix)
        target.save(scanned)
        target.close()
        source.close()

        with mock.patch.dict(os.environ, {'PDF_OCR_ENGINE': 'rapidocr', 'MINERU_COMMAND': ''}):
            with open(scanned, 'rb') as handle:
                response = self.client.post('/api/pipeline/pdf/jobs',
                                            data={'file': (handle, 'scanned_ocr.pdf')},
                                            content_type='multipart/form-data')
        self.assertEqual(response.status_code, 202)
        job = wait_job(self.client, f"/api/pipeline/pdf/jobs/{response.get_json()['id']}", timeout=240)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertTrue(job['result']['ocr'])
        self.assertEqual(job['result']['ocrPages'], 1)
        text = self.client.get(f"/api/pipeline/pdf/jobs/{job['id']}/preview/1").get_data(as_text=True)
        self.assertGreater(len(text), 200)
        self.assertTrue(any(token in text for token in ('人工智能', '智能', '数据')))

    def test_mineru_inline_images_are_extracted(self):
        from pipeline import mineru_adapter
        png = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+'
               'ip1sAAAAASUVORK5CYII=')
        md = f'# 第一章 测试\n\n![图1](data:image/png;base64,{png})\n'
        md_path = self.tmp / 'mineru.md'
        md_path.write_text(md, encoding='utf-8')
        out = self.tmp / 'out'
        text, images, missing = mineru_adapter._collect_images(md, md_path, out, self.tmp)
        self.assertFalse(missing)
        self.assertEqual(len(images), 1)
        self.assertIn('images/inline-0001.png', text)
        self.assertNotIn('data:image', text)
        self.assertTrue((out / 'images' / 'inline-0001.png').is_file())

    def test_mineru_cli_is_preferred_and_keeps_figures(self):
        png = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII='
        )
        script = self.tmp / 'fake_mineru.py'
        script.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "import base64\n"
            "args = sys.argv[1:]\n"
            "out = Path(args[args.index('-o') + 1])\n"
            "src = Path(args[args.index('-p') + 1])\n"
            "folder = out / src.stem / 'auto'\n"
            "(folder / 'images').mkdir(parents=True, exist_ok=True)\n"
            "(folder / 'images' / 'fig1.png').write_bytes(base64.b64decode('"
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII='
            "'))\n"
            "(folder / (src.stem + '.md')).write_text("
            "'# 第1章 扫描章\\n\\n正文甲。\\n\\n![图1](images/fig1.png)\\n\\n## 1.1 小节\\n\\n内容。\\n\\n"
            "# 第2章 扫描二章\\n\\n正文乙。\\n', encoding='utf-8')\n",
            encoding='utf-8',
        )
        scanned = self.tmp / 'mineru_scan.pdf'
        source = pymupdf.open(str(DEMO_PDF))
        pix = source[0].get_pixmap(dpi=90)
        target = pymupdf.open()
        page = target.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pix)
        target.save(scanned)
        target.close()
        source.close()

        env = {
            'PDF_OCR_ENGINE': 'mineru',
            'PDF_OCR': '0',
            'MINERU_COMMAND': f'"{sys.executable}" "{script}"',
        }
        with mock.patch.dict(os.environ, env):
            with open(scanned, 'rb') as handle:
                response = self.client.post('/api/pipeline/pdf/jobs',
                                            data={'file': (handle, 'mineru_scan.pdf')},
                                            content_type='multipart/form-data')
            self.assertEqual(response.status_code, 202)
            job = wait_job(self.client, f"/api/pipeline/pdf/jobs/{response.get_json()['id']}", timeout=120)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertEqual(job['result']['engine'], 'mineru')
        self.assertTrue(job['result']['ocr'])
        self.assertEqual(job['result']['images'], 1)
        self.assertEqual(len(job['chapters']), 2)
        self.assertEqual(job['chapters'][0]['imageCount'], 1)
        self.assertIn('MinerU', '\n'.join(job['log']))

        first = self.client.get(f"/api/pipeline/pdf/jobs/{job['id']}/preview/1").get_data(as_text=True)
        second = self.client.get(f"/api/pipeline/pdf/jobs/{job['id']}/preview/2").get_data(as_text=True)
        self.assertIn('# 第1章 扫描章', first)
        self.assertIn('images/fig1.png', first)
        self.assertIn('# 第2章 扫描二章', second)
        self.assertNotIn('扫描二章', first)

        record = json.loads((self.tmp / 'artifacts' / job['chapters'][0]['artifactId'] / 'artifact.json').read_text(encoding='utf-8'))
        self.assertEqual(len(record['files']['images']), 1)
        image = Path(record['files']['images'][0]).name
        served = self.client.get(f"/api/pipeline/pdf/jobs/{job['id']}/images/1/{image}")
        self.assertEqual(served.status_code, 200)

    def test_strict_mineru_reports_missing_cli(self):
        scanned = self.tmp / 'strict_scan.pdf'
        source = pymupdf.open(str(DEMO_PDF))
        pix = source[0].get_pixmap(dpi=90)
        target = pymupdf.open()
        target.new_page(width=595, height=842).insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pix)
        target.save(scanned)
        target.close()
        source.close()
        env = {'PDF_OCR_ENGINE': 'mineru', 'MINERU_COMMAND': str(self.tmp / 'missing-mineru')}
        with mock.patch.dict(os.environ, env):
            with open(scanned, 'rb') as handle:
                response = self.client.post('/api/pipeline/pdf/jobs',
                                            data={'file': (handle, 'strict_scan.pdf')},
                                            content_type='multipart/form-data')
            job = wait_job(self.client, f"/api/pipeline/pdf/jobs/{response.get_json()['id']}", timeout=60)
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['code'], 'E_MINERU_UNAVAILABLE')
        self.assertIn('MinerU', job['error'])

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

    def test_lesson_from_knowledge_refs_and_handoff_keeps_source(self):
        pdf = self._pdf_job()
        handoff = self.client.post('/api/pipeline/handoff/ppt',
                                   json={'artifactId': pdf['chapters'][0]['artifactId']})
        self.assertEqual(handoff.status_code, 200, handoff.get_data(as_text=True))
        knowledge = [item for item in self.client.get('/api/knowledge').get_json()
                     if item['title'].endswith('.md')]
        self.assertTrue(knowledge)
        ids = [item['id'] for item in knowledge]

        payload = {'offline': True, 'knowledgeRefs': ids}
        with open(TEMPLATE, 'rb') as handle:
            response = self.client.post('/api/pipeline/lesson/jobs', content_type='multipart/form-data', data={
                'payload': json.dumps(payload, ensure_ascii=False),
                'templates': (handle, TEMPLATE.name),
            })
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        job = wait_job(self.client, f"/api/pipeline/lesson/jobs/{response.get_json()['id']}", timeout=300)
        self.assertEqual(job['source']['knowledgeRefs'], ids)
        self.assertIn(job['status'], ('succeeded', 'partial_failed'), job.get('error'))
        ok = next(item for item in job['docx_files'] if item['status'] in ('ok', 'ok_with_warnings'))
        artifact = self.client.post(f"/api/pipeline/lesson/jobs/{job['id']}/artifact/{ok['index']}")
        self.assertEqual(artifact.status_code, 201)
        refs = self.client.post('/api/pipeline/handoff/ppt',
                                json={'artifactId': artifact.get_json()['id']}).get_json()['refs']
        self.assertTrue(any(ref['id'] in ids for ref in refs))

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