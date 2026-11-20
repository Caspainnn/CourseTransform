"""Real DOCX + textbook upload -> model outline. Uses only explicitly provided input paths."""
import argparse
import json
from pathlib import Path
import requests

parser = argparse.ArgumentParser()
parser.add_argument('--lesson', type=Path, required=True)
parser.add_argument('--textbook', type=Path, required=True)
parser.add_argument('--url', default='http://127.0.0.1:7863')
args = parser.parse_args()
s = requests.Session()
s.trust_env = False
def send(path, **kwargs):
    r = s.post(args.url + path, timeout=90, **kwargs)
    r.raise_for_status()
    return r.json()
lesson = send('/api/files', files={'file': (args.lesson.name, args.lesson.read_bytes())})
book = send('/api/files', files={'file': (args.textbook.name, args.textbook.read_bytes())})
entry = send('/api/knowledge', json={'fileId': book['fileId'], 'type': 'jiaocai'})
task = send('/api/tasks', json={'topic': 'GAN生成sin曲线样本实践', 'teacher': '教师', 'audience': '有Python基础的学生',
    'count': 7, 'style': 'academic', 'duration': '45 分钟', 'refs': [entry['id']], 'localRefs': [lesson['fileId']],
    'extra': '依据教材与教案，解释生成器、判别器、交替训练和实验评价。保留来源事实，不加入来源外内容。'})
send('/api/tasks/' + task['id'] + '/outline', json={})
target = Path(__file__).resolve().parents[1] / 'artifacts/upstream.json'
target.parent.mkdir(exist_ok=True)
target.write_text(json.dumps({'task': task['id'], 'lesson': lesson, 'textbook': book, 'knowledge': entry}, ensure_ascii=False, indent=2), encoding='utf-8')
print(task['id'])
