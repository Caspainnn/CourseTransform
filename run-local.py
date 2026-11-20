"""Explicit machine configuration; no experimental-path defaults or embedded keys."""
import json
import os
from pathlib import Path
import runpy

root = Path(__file__).resolve().parent
config = root / 'local-config.json'
if config.exists():
    for key, value in json.loads(config.read_text(encoding='utf-8')).items():
        if key in ('PPTNATIVE_ENV_FILE', 'PPTMASTER_SKILL_DIR', 'PPTNATIVE_RUNTIME_HOST', 'PPTNATIVE_OPENCODE',
               'AUTH_SECRET', 'AUTH_DEV_CODE', 'AUTH_COOKIE_SECURE', 'SMTP_HOST', 'SMTP_PORT', 'SMTP_USER',
               'SMTP_PASS', 'SMTP_FROM', 'SMTP_SSL', 'AUTH_CLAIM_LEGACY'):
            os.environ.setdefault(key, value)
for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
    os.environ.pop(key, None)
runpy.run_path(str(root / 'app.py'), run_name='__main__')
