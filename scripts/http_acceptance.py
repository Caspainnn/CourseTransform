"""Read-only and rejection-path checks against the actual running HTTP service."""
import argparse
import json
from pathlib import Path
import requests

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:7863')
parser.add_argument('--failed-task', required=True)
args = parser.parse_args()
s = requests.Session()
s.trust_env = False
checks = []
def check(name, condition):
    checks.append({'name': name, 'passed': bool(condition)})
    assert condition, name
def get(path):
    return s.get(args.url + path, timeout=20)
page = get('/')
js = get('/static/workbench.js')
health = get('/api/health')
tasks = get('/api/tasks')
knowledge = get('/api/knowledge')
check('real HTML and external JS', page.status_code == js.status_code == 200 and '/static/workbench.js' in page.text)
check('health configured runtime', health.status_code == 200 and health.json()['modelConfigured'] and health.json()['nativeRuntime'])
check('task list JSON', tasks.status_code == 200 and isinstance(tasks.json(), list))
check('real knowledge persisted', knowledge.status_code == 200 and len(knowledge.json()) >= 1)
check('no mock production generators', all(x not in js.text for x in ('BASE_POOL', 'EXTRA_POOL', 'slideHTML', '示例环境', '620)')))
check('no credentials in public payloads', all('LLM_API_KEY' not in r.text and 'Bearer ' not in r.text for r in (page, js, health, tasks, knowledge)))
check('cross-origin write rejected', s.post(args.url+'/api/tasks', json={'topic': 'x'}, headers={'Origin':'https://untrusted.example'}, timeout=15).status_code == 403)
check('zero-page invalid task rejected', s.post(args.url+'/api/tasks', json={'topic':'x','count':0}, timeout=15).status_code == 400)
for path in ('/api/tasks/not-an-id', '/.env', '/static/../.env', '/api/tasks/'+args.failed_task+'/versions/v999/download'):
    check('path rejected '+path, get(path).status_code == 404)
failed = get('/api/tasks/'+args.failed_task).json()
check('failed candidate not current', failed['currentVersion'] is None)
check('failed deck cannot deliver', get('/api/tasks/'+args.failed_task+'/versions/v001/download').status_code == 409)
check('missing preview never becomes template', get('/api/tasks/'+args.failed_task+'/versions/v999/pages/1.png').status_code == 404)
check('stale title feedback rejected', s.post(args.url+'/api/tasks/'+args.failed_task+'/slides/0/revise', json={'base_version':'v999','pageId':'p01','text':'标题修改'},timeout=15).status_code == 409)
check('stale confirmation rejected', s.post(args.url+'/api/tasks/'+args.failed_task+'/confirm', json={'base_version':'v999'},timeout=15).status_code == 409)
target = Path(__file__).resolve().parents[1] / 'artifacts/http-acceptance.json'
target.parent.mkdir(exist_ok=True)
target.write_text(json.dumps({'passed': True, 'checks': checks},ensure_ascii=False,indent=2),encoding='utf-8')
print(f'{len(checks)} real HTTP checks passed')
