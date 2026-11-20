"""End-to-end smoke test of the integrated stages through the Flask app.

Usage:
  python scripts/pipeline_smoke.py --pdf <pdf> --template <docx> [--offline]

Exercises PDF -> MD, artifact registration, MD -> lesson DOCX (offline engine
by default), then registers the lesson for the PPT stage. It does not call a
model unless --real-api and JIAOAN_* are configured.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402


def wait_pdf(client, ident, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f'/api/pipeline/pdf/jobs/{ident}').get_json()
        if data['status'] not in ('queued', 'running'):
            return data
        time.sleep(0.5)
    raise SystemExit('PDF job timeout')


def wait_lesson(client, ident, timeout=600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f'/api/pipeline/lesson/jobs/{ident}').get_json()
        if data['status'] not in ('queued', 'running'):
            return data
        time.sleep(1)
    raise SystemExit('lesson job timeout')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pdf', required=True)
    parser.add_argument('--template', required=True)
    parser.add_argument('--offline', action='store_true', default=True)
    parser.add_argument('--subject', default='信息科技')
    parser.add_argument('--grade', default='八年级')
    args = parser.parse_args()

    data_dir = Path(tempfile.mkdtemp(prefix='ct-smoke-'))
    app = create_app(data_dir)
    client = app.test_client()

    with open(args.pdf, 'rb') as handle:
        response = client.post('/api/pipeline/pdf/jobs', data={'file': (handle, Path(args.pdf).name)},
                               content_type='multipart/form-data')
    print('pdf create', response.status_code, response.get_json())
    assert response.status_code == 202, response.get_data(as_text=True)
    pdf_job = wait_pdf(client, response.get_json()['id'])
    print('pdf job', pdf_job['status'], pdf_job.get('error'), [c['title'] for c in pdf_job['chapters']])
    assert pdf_job['status'] == 'succeeded', pdf_job
    artifact_ids = [chapter['artifactId'] for chapter in pdf_job['chapters']]
    md = client.get(f"/api/pipeline/pdf/jobs/{pdf_job['id']}/preview/1")
    print('md preview', md.status_code, len(md.get_data(as_text=True)))
    assert md.status_code == 200 and '# ' in md.get_data(as_text=True)

    payload = {'offline': True, 'subject': args.subject, 'grade': args.grade,
               'duration': 45, 'textbookArtifacts': artifact_ids}
    with open(args.template, 'rb') as handle:
        response = client.post('/api/pipeline/lesson/jobs', content_type='multipart/form-data', data={
            'payload': json.dumps(payload, ensure_ascii=False),
            'templates': (handle, Path(args.template).name),
        })
    print('lesson create', response.status_code, response.get_json())
    assert response.status_code == 202, response.get_data(as_text=True)
    lesson = wait_lesson(client, response.get_json()['id'])
    print('lesson job', lesson['status'], lesson.get('error'),
          [x['status'] for x in lesson.get('chapters', [])])
    assert lesson['status'] in ('succeeded', 'partial_failed'), lesson
    assert lesson['docx_files'], lesson

    docx = client.get(lesson['docx_files'][0]['url'])
    print('docx', docx.status_code, len(docx.data))
    assert docx.status_code == 200 and docx.data[:2] == b'PK'

    ok_index = next((item['index'] for item in lesson['docx_files']
                     if item['status'] in ('ok', 'ok_with_warnings')), None)
    assert ok_index, lesson['docx_files']
    response = client.post(f"/api/pipeline/lesson/jobs/{lesson['id']}/artifact/{ok_index}")
    print('lesson artifact', response.status_code, response.get_json().get('id'))
    assert response.status_code == 201
    artifact_id = response.get_json()['id']
    response = client.post('/api/pipeline/handoff/ppt', json={'artifactId': artifact_id})
    handoff = response.get_json()
    print('handoff', response.status_code, json.dumps(handoff, ensure_ascii=False))
    assert response.status_code == 200 and handoff['refs']
    knowledge = client.get('/api/knowledge').get_json()
    print('knowledge', [(x['title'], x['type']) for x in knowledge])
    print('SMOKE OK', data_dir)


if __name__ == '__main__':
    main()