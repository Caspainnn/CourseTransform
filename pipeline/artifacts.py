"""Stage artifact registry: immutable hand-off records between pipeline stages.

Each artifact keeps the bytes identity (sha256), the producing job, chapter and
the files the next stage needs (Markdown, referenced images, original PDF, DOCX).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

ARTIFACT_ID = re.compile(r'^[a-f0-9]{32}$')


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root) / 'artifacts'
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, ident):
        if not isinstance(ident, str) or not ARTIFACT_ID.fullmatch(ident):
            return None
        path = self.root / ident
        return path if path.is_dir() else None

    def get(self, ident):
        folder = self.folder(ident)
        if not folder:
            return None
        return read_json(folder / 'artifact.json')

    def list(self, stage=None, limit=40):
        items = []
        for path in self.root.glob('*/artifact.json'):
            record = read_json(path)
            if not record:
                continue
            if stage and record.get('stage') != stage:
                continue
            items.append(record)
        items.sort(key=lambda x: x.get('created', ''), reverse=True)
        return items[:limit]

    def register(self, *, stage, title, source, chapter=None, files=None, meta=None):
        """Copy provided files into a new immutable artifact folder."""
        ident = uuid.uuid4().hex
        folder = self.root / ident
        folder.mkdir()
        stored = {}
        for key, value in (files or {}).items():
            if value is None:
                continue
            target_dir = folder / 'images' if key == 'images' else folder
            if isinstance(value, (list, tuple)):
                rels = []
                for item in value:
                    rel = self._copy(target_dir, Path(item))
                    if rel:
                        rels.append('images/' + rel if key == 'images' else rel)
                stored[key] = rels
            else:
                rel = self._copy(target_dir, Path(value))
                if rel:
                    stored[key] = 'images/' + rel if key == 'images' else rel
        record = {
            'id': ident,
            'stage': stage,
            'title': title,
            'chapter': chapter or {},
            'source': source or {},
            'files': stored,
            'meta': meta or {},
            'created': now(),
        }
        write_json(folder / 'artifact.json', record)
        return record

    @staticmethod
    def _copy(folder, path):
        if not path.is_file():
            return None
        folder.mkdir(parents=True, exist_ok=True)
        name = path.name
        target = folder / name
        if target.exists():
            target = folder / f'{path.stem}-{uuid.uuid4().hex[:6]}{path.suffix}'
        shutil.copy2(path, target)
        return target.name

    def file_path(self, record, key, index=None):
        folder = self.folder(record.get('id'))
        if not folder:
            return None
        value = (record.get('files') or {}).get(key)
        if value is None:
            return None
        if isinstance(value, list):
            if index is None or not 0 <= index < len(value):
                return None
            value = value[index]
        path = (folder / value).resolve()
        if not path.is_file() or not path.is_relative_to(folder.resolve()):
            return None
        return path

    def update(self, record):
        folder = self.folder(record.get('id'))
        if not folder:
            return False
        write_json(folder / 'artifact.json', record)
        return True