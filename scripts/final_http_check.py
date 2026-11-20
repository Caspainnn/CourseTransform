"""Verify persisted products and HEAD delivery metadata; never download deck bodies."""
import argparse
import hashlib
import json
from pathlib import Path

import requests

parser = argparse.ArgumentParser()
parser.add_argument('--original', required=True)
parser.add_argument('--upstream', required=True)
parser.add_argument('--confirm-technical', action='store_true', help='Confirm only the separate upstream technical test')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
session = requests.Session()
session.trust_env = False
base = 'http://127.0.0.1:7863'
checks = []

def check(name, passed):
    checks.append({'name': name, 'passed': bool(passed)})
    assert passed, name

check('old service still running', session.get('http://127.0.0.1:7862/api/health', timeout=15).status_code == 200)
check('knowledge persisted', len(session.get(base + '/api/knowledge', timeout=15).json()) >= 1)
for label, ident in [('original', args.original), ('upstream', args.upstream)]:
    kernel = root / 'data/tasks' / ident / 'kernel'
    job = json.loads((kernel / 'job.json').read_text(encoding='utf-8'))
    current = job['current']
    response = session.get(base + '/api/tasks/' + ident, timeout=15)
    check(label + ' persisted task', response.status_code == 200 and len(response.json()['outline']) == 7)
    if label == 'upstream' and args.confirm_technical:
        response = session.post(base + '/api/tasks/' + ident + '/confirm', json={'base_version': current}, timeout=30)
        check('technical confirmation only', response.status_code == 200 and response.json()['status'] == 'confirmed')
    for vid in job['history']:
        folder = kernel / 'versions' / vid
        deck = folder / 'deck.pptx'
        head = session.head(base + f'/api/tasks/{ident}/versions/{vid}/download', timeout=15)
        check(label + ' ' + vid + ' HEAD', head.status_code == 200 and not head.content
              and int(head.headers['Content-Length']) == deck.stat().st_size
              and head.headers['X-Content-SHA256'] == hashlib.sha256(deck.read_bytes()).hexdigest())
    for number in range(1, 8):
        preview = session.get(base + f'/api/tasks/{ident}/versions/{current}/pages/{number}.png', timeout=15)
        check(label + f' real PNG {number}', preview.status_code == 200 and preview.content == (kernel / 'versions' / current / 'preview' / f'deck_p{number}.png').read_bytes())
report = {'passed': True, 'checks': checks, 'browser_download_tested': False, 'user_acceptance': False}
(root / 'artifacts').mkdir(exist_ok=True)
(root / 'artifacts/final-http-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'{len(checks)} persisted/metadata/preview checks passed; no PPTX response body requested')
