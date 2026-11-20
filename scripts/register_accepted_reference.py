"""Register reading evidence from an explicitly user-confirmed job; never imports a product version."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pptnative.models import read, save, ROOT
from pptnative.geometry import regions
from pptx import Presentation

parser = argparse.ArgumentParser()
parser.add_argument('--job', type=Path, required=True)
parser.add_argument('--version', default='v001')
args = parser.parse_args()
job = args.job.resolve()
version = job / 'versions' / args.version
manifest = read(version / 'version.json')
if manifest['status'] != 'confirmed' or not manifest.get('qa_passed'):
    raise SystemExit('Only explicitly confirmed, QA-passed evidence can calibrate reading requirements')
template_sha = hashlib.sha256((job / 'input/template.pptx').read_bytes()).hexdigest()
directory = ROOT / '.pptnative-runtime/accepted-pages' / template_sha
directory.mkdir(parents=True, exist_ok=True)
deck = Presentation(version / 'deck.pptx')
material = read(job / 'input/material-pool.json')
records = []
for number, page in enumerate(read(version / 'plan.json')['pages'], 1):
    preview = version / 'preview' / f'deck_p{number}.png'
    target = directory / preview.name
    shutil.copyfile(preview, target)
    records.append({'title': page['title'], 'notes': page.get('notes', ''), 'purpose': page.get('purpose', ''),
        'facts': [material['facts'][r] for r in page['point_ids']],
        'asset_sha256': [material['assets'][r]['sha256'] for r in page['asset_ids']], 'preview': target.name,
        'preview_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
        'actual_image_sizes': [r['frame_box'][2:] for r in regions(deck.slides[number-1], 1280/deck.slide_width) if 'crop' in r]})
save(directory / 'reference.json', {'template_sha256': template_sha, 'source_job': job.name, 'source_version': args.version,
    'basis': 'Explicitly user-confirmed; reference PNG and content fingerprints only, no finished deck imported', 'pages': records})
print(f'Registered {len(records)} reading references; no task or product version created')
