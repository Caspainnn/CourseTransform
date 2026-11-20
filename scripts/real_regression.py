"""Authorized original seven-page upload, fresh production authoring, no finished-deck import."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import requests

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True, help='read-only historical materials root')
parser.add_argument('--url', default='http://127.0.0.1:7863')
parser.add_argument('--start-only', action='store_true')
args = parser.parse_args()
session = requests.Session()
session.trust_env = False
source = args.source.resolve()
template = source / 'experiments/pptnative-native-test12-v2/input/template.pptx'
outline = source / 'experiments/skill-template-trial/runs/ppt-master/textbook-tier-trial-v2/minimal/outline.md'
assets = source / 'experiments/skill-template-trial/runs/ppt-master/textbook-repeat/textbook_files'
names = ['image_005.jpg', 'image_007.jpg', 'image_012.jpg', 'image_013.jpg', 'image_014.jpg', 'image_015.jpg']
files = [('template', (template.name, template.read_bytes())), ('outline', ('outline.md', outline.read_bytes()))]
files += [('assets', (name, (assets / name).read_bytes())) for name in names]
r = session.post(args.url + '/api/tasks/from-approved-outline', files=files, timeout=60)
r.raise_for_status()
task = r.json()
report = {'task': task['id'], 'source': 'original source material upload; fresh production creation',
          'template_sha256': hashlib.sha256(template.read_bytes()).hexdigest(),
          'outline_sha256': hashlib.sha256(outline.read_bytes()).hexdigest(), 'assets': names}
target = Path(__file__).resolve().parents[1] / 'artifacts/original-seven.json'
target.parent.mkdir(exist_ok=True)
target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
r = session.post(args.url + '/api/tasks/' + task['id'] + '/generate', json={}, timeout=20)
r.raise_for_status()
print(json.dumps(report, ensure_ascii=False), flush=True)
if args.start_only:
    sys.exit()
while True:
    task = session.get(args.url + '/api/tasks/' + task['id'], timeout=30).json()
    print(task['status'], task.get('events', [])[-1:] or task.get('error'), flush=True)
    if task['status'] not in ('queued', 'running'):
        report['result'] = task
        target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        break
    time.sleep(15)
assert task['status'] == 'ready', task.get('error')
