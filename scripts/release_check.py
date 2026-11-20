"""Inspect the actual Git candidate set without disclosing credential values."""
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
config = root / 'local-config.json'
if config.exists():
    local = json.loads(config.read_text(encoding='utf-8'))
    if local.get('PPTNATIVE_ENV_FILE'):
        os.environ.setdefault('PPTNATIVE_ENV_FILE', local['PPTNATIVE_ENV_FILE'])
from pptnative.api import settings
key = settings().get('LLM_API_KEY', '')
output = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root)
files = list(dict.fromkeys(x.decode('utf-8') for x in output.split(b'\0') if x))
issues = []
for name in files:
    path = root / name
    if not path.is_file():
        continue
    if name.startswith(('data/', 'artifacts/', '.pptnative-runtime/', 'runtime/')) or path.suffix.lower() in ('.pptx', '.potx', '.pdf', '.docx', '.jpg', '.png'):
        issues.append({'file': name, 'reason': 'runtime/material artifact in Git candidate'})
    if path.name == '.env' or name == 'local-config.json':
        issues.append({'file': name, 'reason': 'machine configuration in Git candidate'})
    content = path.read_bytes()
    if key and key.encode() in content:
        issues.append({'file': name, 'reason': 'configured credential detected'})
    if len(content) > 2*1024**2:
        issues.append({'file': name, 'reason': 'unexpected large source candidate'})
report = {'passed': not issues, 'candidate_files': len(files), 'issues': issues,
          'remote_count': len(subprocess.check_output(['git', 'remote'], cwd=root).splitlines())}
target = root / 'artifacts/release-check.json'
target.parent.mkdir(exist_ok=True)
target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True))
if issues or report['remote_count']:
    raise SystemExit(1)
