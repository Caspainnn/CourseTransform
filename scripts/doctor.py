"""Print only dependency/configuration presence, never credential values."""
import importlib.util
import json
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
config = root / 'local-config.json'
if config.exists():
    for key, value in json.loads(config.read_text(encoding='utf-8')).items():
        os.environ.setdefault(key, value)
from pptnative.api import settings
from pptnative.native import SKILL, HOST
checks = {name: importlib.util.find_spec(name) is not None for name in ('flask', 'pptx', 'PIL', 'lxml', 'pypdf')}
checks['windows'] = os.name == 'nt'
checks['model_configured'] = all(settings().get(k) for k in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL', 'LLM_OUTLINE_JSON_MODEL'))
checks['original_ppt_master'] = all((SKILL / x).exists() for x in ('SKILL.md', 'scripts/svg_to_pptx.py', 'scripts/pptx_to_svg.py', 'workflows/edit-native-pptx.md'))
checks['runtime_python'] = (HOST / 'python').exists()
if os.name == 'nt':
    import winreg
    for name in ('PowerPoint.Application', 'Word.Application'):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, name):
                checks[name] = True
        except OSError:
            checks[name] = False
print(json.dumps(checks, indent=2))
if not all(v for k, v in checks.items() if k != 'Word.Application'):
    raise SystemExit(1)
