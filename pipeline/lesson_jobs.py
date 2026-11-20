"""Lecture-plan stage adapter around the verified jiaoan engine.

The engine (jiaoan/*) is kept as-is; this module only creates the same job
structure its web entrypoint uses, runs it on a background thread, persists
recoverable snapshots and exposes stage results to the unified Flask service.
"""
from __future__ import annotations

import json
import shutil
import threading
import uuid
from pathlib import Path

from documents import Problem
from jiaoan import web as jweb
from jiaoan.utils import safe_name

STAGES = {'queued', 'running'}
DONE_FAIL = {'failed', 'partial_failed', 'quality_failed'}

_patched = False


def _now():
    from jiaoan.utils import now_iso
    return now_iso()


class LessonJobManager:
    def __init__(self, root: Path):
        self.root = Path(root) / 'lesson'
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._recover()

    # ---- persistence -------------------------------------------------

    def folder(self, ident):
        if not isinstance(ident, str) or not ident.isalnum() or not 8 <= len(ident) <= 32:
            return None
        path = self.root / ident
        return path if path.is_dir() else None

    def _snapshot(self, job):
        folder = Path(job.get('dir') or '')
        if not folder.is_dir():
            return
        safe = {key: value for key, value in job.items() if key not in ('api', 'image_hints')}
        safe['snapshotAt'] = _now()
        (folder / 'job.json').write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding='utf-8')

    def _recover(self):
        for path in sorted(self.root.glob('*/job.json')):
            record = self._read(path)
            if not record:
                continue
            if record.get('status') in STAGES:
                record.update(status='interrupted', operation=None,
                              error='服务重启导致任务中断。输入文件已保留，可在页面上重试。')
                path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')

    @staticmethod
    def _read(path):
        try:
            return json.loads(Path(path).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def get(self, ident):
        job = jweb.JOBS.get(ident)
        if job:
            return job
        folder = self.folder(ident)
        if not folder:
            return None
        return self._read(folder / 'job.json')

    # ---- creation ----------------------------------------------------
    def create(self, *, textbook_path, templates, images, payload, source=None):
        ident = uuid.uuid4().hex[:12]
        folder = self.root / ident
        input_dir = folder / 'input'
        tpl_dir = input_dir / 'templates'
        image_dir = input_dir / 'images'
        input_dir.mkdir(parents=True)
        tpl_dir.mkdir()
        image_dir.mkdir()

        shutil.copy2(textbook_path, input_dir / 'textbook.md')
        template_paths = []
        for item in templates:
            name = safe_name(Path(item.filename).name, 80)
            suffix = Path(name).suffix.lower()
            if suffix not in {'.docx', '.md', '.markdown'}:
                raise Problem(f'不支持的模板格式：{name}')
            target = tpl_dir / name
            item.save(target)
            template_paths.append(target)

        captions = payload.get('captions') or {}
        image_hints = {}
        image_count = 0
        for item in images:
            name = safe_name(Path(item.filename).name, 80)
            if Path(name).suffix.lower() not in jweb.IMAGE_EXTS:
                raise Problem(f'不支持的图片格式：{name}')
            target = image_dir / name
            if target.exists():
                target = image_dir / f'{Path(name).stem}_{image_count}{Path(name).suffix}'
            item.save(target)
            caption = str(captions.get(item.filename) or captions.get(name) or '').strip()
            if caption:
                image_hints[target.name] = caption
                image_hints[Path(target.name).stem] = caption
            image_count += 1

        brief = {
            'subject': str(payload.get('subject') or '信息科技').strip(),
            'grade': str(payload.get('grade') or '八年级').strip(),
            'duration_minutes': int(payload.get('duration') or 45),
            'lesson_type': str(payload.get('lesson_type') or '新授课').strip(),
            'student_profile': str(payload.get('student_profile') or '').strip(),
        }
        if payload.get('periods'):
            brief['periods'] = int(payload['periods'])
            brief['periods_explicit'] = True
        (input_dir / 'brief.json').write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding='utf-8')

        if len(template_paths) == 1:
            template_arg = str(template_paths[0])
        elif len(template_paths) > 1:
            template_arg = str(tpl_dir)
        else:
            template_arg = None

        offline = bool(payload.get('offline'))
        api, api_public = self._api(payload.get('api'), offline)
        job = {
            'id': ident,
            'status': 'queued',
            'created_at': _now(),
            'dir': str(folder),
            'out_dir': str(folder / 'out'),
            'offline': offline,
            'review': bool(payload.get('review')),
            'brief': brief,
            'chapters': str(payload.get('chapters') or 'all').strip() or 'all',
            'template_arg': template_arg,
            'image_dir': str(image_dir) if image_count else '',
            'image_hints': image_hints,
            'image_count': image_count,
            'filter_images': bool(payload.get('filter_images', True)),
            'keep_all_images': bool(payload.get('keep_all_images', False)),
            'api': api,
            'api_public': api_public,
            'log': str(folder / 'run.log'),
            'progress': {'current': 0, 'total': 0},
            'source': source or {},
            'templates': [item.name for item in template_paths],
        }
        self._snapshot(job)
        with self.lock:
            jweb.JOBS[ident] = job
        threading.Thread(target=self._watch, args=(ident,), daemon=True).start()
        threading.Thread(target=self._run, args=(ident,), daemon=True).start()
        return ident

    @staticmethod
    def _api(api_in, offline):
        if offline:
            return {}, {'label': '离线演示', 'base_url': '', 'model': 'offline'}
        if isinstance(api_in, dict) and api_in.get('base_url') and api_in.get('model'):
            base = jweb._normalize_base_url(str(api_in.get('base_url')))
            parsed = jweb.urlparse(base)
            if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
                raise Problem('Base URL 格式不正确，应以 http:// 或 https:// 开头')
            key = str(api_in.get('api_key') or '').strip()
            if not key and not jweb._is_local_base(base):
                raise Problem('该接口需要 API Key')
            api = {
                'base_url': base,
                'api_key': key or jweb.LOCAL_KEY,
                'model': str(api_in.get('model')),
                'label': str(api_in.get('label') or '自定义 API'),
                'timeout': int(api_in.get('timeout') or 900),
                'max_tokens': int(api_in.get('max_tokens') or 8192),
                'max_context_chars': int(api_in.get('max_context_chars') or 12000),
                'disable_thinking': api_in.get('disable_thinking') if isinstance(api_in.get('disable_thinking'), bool) else None,
            }
            return api, jweb._public_api(api)
        # No explicit API: the server-side .env is the only source (JIAOAN_*).
        return {}, {'label': '服务器 .env', 'base_url': '', 'model': ''}

    def _watch(self, ident):
        last = None
        while True:
            job = jweb.JOBS.get(ident)
            if not job:
                return
            marker = (job.get('status'), json.dumps(job.get('progress'), sort_keys=True),
                      len(job.get('results') or []))
            if marker != last:
                try:
                    self._snapshot(job)
                except Exception:
                    pass
                last = marker
            if job.get('status') not in STAGES:
                return
            threading.Event().wait(1.5)

    def _run(self, ident):
        try:
            jweb._run_job(ident)
        finally:
            job = jweb.JOBS.get(ident)
            if job:
                try:
                    self._snapshot(job)
                except Exception:
                    pass
                # Never keep the key in memory longer than the run needs it.
                job.pop('api', None)

    # ---- views -------------------------------------------------------
    def public(self, job):
        if not job:
            return None
        public = jweb._job_public(job)
        entries = []
        for item in self._flat_docx(job):
            entries.append({'index': len(entries) + 1, 'chapter_id': item['chapterId'],
                            'title': item['title'], 'url': f'/api/pipeline/lesson/jobs/{job["id"]}/docx/{len(entries) + 1}',
                            'status': item['status'],
                            'previewUrl': f'/api/pipeline/lesson/jobs/{job["id"]}/preview/{item["chapterIndex"]}'})
        public['docx_files'] = entries
        if not isinstance(public.get('chapters'), list):
            public['chapters'] = []
        results = job.get('results') or []
        result_by_chapter = {}
        for chapter_index, result in enumerate(results, start=1):
            result_by_chapter[result.get('chapter_id')] = (chapter_index, result)
        for chapter in public['chapters']:
            pair = result_by_chapter.get(chapter.get('id'))
            if not pair:
                continue
            chapter_index, result = pair
            files = result.get('files') or {}
            chapter['chapterIndex'] = chapter_index
            chapter['previewUrl'] = f'/api/pipeline/lesson/jobs/{job["id"]}/preview/{chapter_index}'
            chapter['downloads'] = {
                label: f'/api/pipeline/lesson/jobs/{job["id"]}/file/{chapter_index}/{key}'
                for label, key in (('plan', 'plan'), ('review', 'review'), ('acceptance', 'acceptance'), ('image_report', 'image_report'))
                if files.get(key)
            }
        public['source'] = job.get('source') or {}
        public['templates'] = job.get('templates') or []
        public['image_count'] = job.get('image_count', 0)
        public['brief'] = job.get('brief') or {}
        public['offline'] = bool(job.get('offline'))
        public['recoverable'] = job.get('status') in {'interrupted', 'failed', 'partial_failed', 'quality_failed'}
        public['progressText'] = self._progress_text(job)
        return public

    @staticmethod
    def _progress_text(job):
        progress = job.get('progress') or {}
        current, total = progress.get('current') or 0, progress.get('total') or 0
        status = job.get('status')
        if status == 'queued':
            return '排队中'
        if status == 'running':
            title = progress.get('title') or ''
            return f'生成中 {current}/{total} 章 · {title}'
        return {'succeeded': '全部学时生成完成', 'partial_failed': '部分章节失败，成功产物可下载',
                'quality_failed': '质检未通过，不能作为可交付结果', 'failed': '生成失败',
                'interrupted': '中断，可重试'}.get(status, status or '')

    def _flat_docx(self, job):
        flat = []
        for chapter_index, result in enumerate(job.get('results') or [], start=1):
            chapter_id = result.get('chapter_id') or ''
            status = result.get('status') or 'failed'
            for item in result.get('docx_files') or []:
                path = Path(str(item.get('path') or ''))
                if path.is_file():
                    flat.append({'path': path, 'title': str(item.get('title') or result.get('title') or '教案'),
                                 'status': status, 'chapterId': chapter_id, 'chapterIndex': chapter_index})
            if not result.get('docx_files'):
                path = Path(str((result.get('files') or {}).get('docx') or ''))
                if path.is_file():
                    flat.append({'path': path, 'title': str(result.get('title') or '教案'),
                                 'status': status, 'chapterId': chapter_id, 'chapterIndex': chapter_index})
        return flat

    def docx_path(self, ident, index):
        job = self.get(ident)
        if not job:
            return None
        flat = self._flat_docx(job)
        if not flat or not 0 < index <= len(flat):
            return None
        return flat[index - 1]['path']

    def docx_meta(self, ident, index):
        job = self.get(ident)
        if not job:
            return None
        flat = self._flat_docx(job)
        if not flat or not 0 < index <= len(flat):
            return None
        return flat[index - 1]

    def preview_md(self, ident, index=1):
        job = self.get(ident)
        if not job:
            return None
        results = job.get('results') or []
        if not 0 < index <= len(results):
            return None
        path = (results[index - 1].get('files') or {}).get('md') or ''
        return Path(path) if Path(path).is_file() else None

    def log(self, ident):
        job = self.get(ident)
        if not job:
            return None
        path = Path(job.get('log') or '')
        if not path.is_file():
            return None
        text = path.read_text(encoding='utf-8', errors='replace')
        # Keys are never written, but redact defensively in case a model echoes one.
        return text[-20000:]

    def retry(self, ident, api_payload=None):
        job = self.get(ident)
        if not job:
            raise Problem('任务不存在', 404, 'E_NOT_FOUND')
        folder = Path(job['dir'])
        input_dir = folder / 'input'
        if not (input_dir / 'textbook.md').is_file():
            raise Problem('原任务输入文件缺失，无法重试', 409, 'E_INPUT_MISSING')
        new_id = uuid.uuid4().hex[:12]
        new_folder = self.root / new_id
        new_folder.mkdir()
        shutil.copytree(input_dir, new_folder / 'input')
        api, api_public = self._api(api_payload, bool(job.get('offline')))
        updated = dict(job)
        updated.update(id=new_id, dir=str(new_folder), out_dir=str(new_folder / 'out'),
                       log=str(new_folder / 'run.log'), status='queued', operation=None, error=None,
                       created_at=_now(), progress={'current': 0, 'total': 0},
                       api=api, api_public=api_public, results=[], chapters=[])
        self._snapshot(updated)
        with self.lock:
            jweb.JOBS[new_id] = updated
        threading.Thread(target=self._watch, args=(new_id,), daemon=True).start()
        threading.Thread(target=self._run, args=(new_id,), daemon=True).start()
        return new_id