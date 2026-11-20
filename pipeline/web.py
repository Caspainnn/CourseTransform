"""Unified workbench routes for the PDF->MD and MD->教案 stages.

Mounted as a Flask blueprint from app.create_app so all three stages share the
same local-only server, file/knowledge store and artifact hand-off records.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import threading
import uuid
import zipfile
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from werkzeug.datastructures import FileStorage

from documents import Problem, decode, parse_document
from jiaoan import web as jweb
from pipeline.artifacts import ARTIFACT_ID, ArtifactStore, now, read_json, sha256_file, write_json
from pipeline.lesson_jobs import LessonJobManager
from pipeline import pdf2md

IMAGE_REF = re.compile(r'!\[[^\]]*\]\((images/[^)\s]+)\)')
JOB_ID = re.compile(r'^[a-f0-9]{8,32}$')


def _copy_images(artifact, record, target_dir, log):
    """Copy only the images referenced by the artifact Markdown, keeping names."""
    folder = Path(artifact['_folder'])
    md = record['files'].get('md')
    names = []
    for md_name in ([md] if isinstance(md, str) else (md or [])):
        text = (folder / md_name).read_text(encoding='utf-8')
        names.extend(IMAGE_REF.findall(text))
    seen = set()
    for rel in names:
        name = Path(rel).name
        if name in seen:
            continue
        seen.add(name)
        source = folder / 'images' / name
        if not source.is_file():
            source = folder / name
        if source.is_file():
            shutil.copy2(source, target_dir / name)
        else:
            log(f'缺少图片 {name}，已跳过')
    return len(seen)


class PipelineService:
    def __init__(self, data_dir: Path):
        self.data = Path(data_dir)
        self.root = self.data / 'pipeline'
        (self.root / 'pdf').mkdir(parents=True, exist_ok=True)
        (self.root / 'lesson').mkdir(parents=True, exist_ok=True)
        self.artifacts = ArtifactStore(self.data)
        self.lessons = LessonJobManager(self.root)
        self.lock = threading.Lock()
        self._recover_pdf()

    # ---- shared helpers ---------------------------------------------
    def _pdf_folder(self, ident):
        if not isinstance(ident, str) or not JOB_ID.fullmatch(ident):
            return None
        folder = self.root / 'pdf' / ident
        return folder if folder.is_dir() else None

    def _pdf_job(self, ident):
        folder = self._pdf_folder(ident)
        if not folder:
            return None
        return read_json(folder / 'job.json')

    def _save_pdf_job(self, job):
        folder = self._pdf_folder(job['id'])
        if folder:
            write_json(folder / 'job.json', job)

    def _recover_pdf(self):
        for path in sorted((self.root / 'pdf').glob('*/job.json')):
            job = read_json(path)
            if job and job.get('status') in ('queued', 'running'):
                job.update(status='interrupted', error='服务重启导致任务中断。原始 PDF 与已完成页面已保留，可重试。')
                write_json(path, job)

    def _new_pdf(self, upload):
        name = Path(upload.filename.replace('\\', '/')).name
        suffix = Path(name).suffix.lower()
        if suffix != '.pdf':
            raise Problem('仅支持 PDF 文件')
        content = upload.read(30 * 1024**2 + 1)
        if not content or len(content) > 30 * 1024**2:
            raise Problem('PDF 须在 1 字节至 30MB 之间')
        if content[:5] != b'%PDF-':
            raise Problem('文件不是有效的 PDF', code='E_DOCUMENT')
        ident = uuid.uuid4().hex[:12]
        folder = self.root / 'pdf' / ident
        folder.mkdir()
        (folder / 'source.pdf').write_bytes(content)
        job = {
            'id': ident, 'status': 'queued', 'created': now(), 'updated': now(),
            'fileName': name, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(),
            'log': [], 'chapters': [], 'error': None, 'result': None, 'sourcePath': str(folder / 'source.pdf'),
        }
        self._save_pdf_job(job)
        threading.Thread(target=self._run_pdf, args=(ident,), daemon=True).start()
        return job

    def _run_pdf(self, ident):
        folder = self.root / 'pdf' / ident
        job = read_json(folder / 'job.json')
        job.update(status='running', updated=now())
        self._save_pdf_job(job)

        def log(message):
            job['log'].append(str(message))
            job['updated'] = now()
            self._save_pdf_job(job)

        try:
            result = pdf2md.run_job(folder, log=log)
            artifacts = []
            for chapter in result['chapters']:
                record = self.artifacts.register(
                    stage='pdf2md',
                    title=chapter['title'],
                    chapter={'index': chapter['index'], 'title': chapter['title'], 'pages': chapter['pages']},
                    source={'jobId': ident, 'pdfName': job['fileName'], 'pdfSha256': job['sha256'],
                            'pdfPath': job['sourcePath']},
                    files={'md': chapter['md'], 'images': chapter['images']},
                    meta={'chars': chapter['chars'], 'imageCount': chapter['imageCount'],
                          'mdName': chapter['mdName']},
                )
                artifacts.append(record['id'])
                chapter['artifactId'] = record['id']
                chapter.pop('md', None)
            job.update(status='succeeded', result=result, chapters=result['chapters'],
                       artifactIds=artifacts, updated=now())
            log(f"完成：{len(result['chapters'])} 个章节，{result['images']} 张图片。")
        except Problem as error:
            job.update(status='failed', error=error.message, code=error.code, updated=now())
            job['log'].append(f'失败：{error.message}')
        except Exception as error:  # noqa: BLE001
            job.update(status='failed', error=f'{type(error).__name__}: {error}',
                       code='E_RUNTIME', updated=now())
            job['log'].append(f'失败：{type(error).__name__}: {error}')
        self._save_pdf_job(job)

    def _pdf_public(self, job):
        public = {key: job.get(key) for key in
                  ('id', 'status', 'created', 'updated', 'fileName', 'size', 'sha256', 'error', 'code')}
        public['chapters'] = [
            {key: chapter.get(key) for key in
             ('index', 'title', 'mdName', 'chars', 'pages', 'imageCount', 'artifactId', 'revision', 'edited')}
            for chapter in (job.get('chapters') or [])
        ]
        public['log'] = (job.get('log') or [])[-200:]
        public['result'] = {key: (job.get('result') or {}).get(key) for key in
                            ('pages', 'images', 'ocr', 'ocrPages', 'ocrConfidence', 'ocrDpi',
                             'engine', 'mineruTier', 'mineruSeconds')}
        return public

    def save_pdf_chapter(self, ident, index, markdown):
        """Store an edited chapter as a new immutable artifact revision."""
        job = self._pdf_job(ident)
        if not job:
            raise Problem('任务不存在', 404, 'E_NOT_FOUND')
        chapters = job.get('chapters') or []
        if not 0 < index <= len(chapters):
            raise Problem('章节不存在', 404, 'E_NOT_FOUND')
        if job.get('status') != 'succeeded':
            raise Problem('任务尚未完成，无法保存修订', 409, 'E_JOB_NOT_READY')
        text = str(markdown or '').replace('\r\n', '\n')
        if not text.strip():
            raise Problem('章节内容不能为空', 400, 'E_EMPTY_CONTENT')
        if len(text.encode('utf-8')) > 2 * 1024**2:
            raise Problem('章节内容超过 2MB，请拆分后再保存', 400, 'E_CONTENT_TOO_LARGE')

        chapter = chapters[index - 1]
        artifact = self.artifacts.get(chapter.get('artifactId') or '')
        if not artifact:
            raise Problem('章节产物不存在', 404, 'E_NOT_FOUND')
        folder = self.artifacts.folder(artifact['id'])
        md_name = (artifact.get('meta') or {}).get('mdName') or f'{chapter["title"]}.md'
        target = self.root / 'pdf' / ident / 'out' / 'edited'
        target.mkdir(parents=True, exist_ok=True)
        md_path = target / Path(md_name).name
        md_path.write_text(text, encoding='utf-8', newline='\n')

        images = []
        for rel in (artifact.get('files') or {}).get('images') or []:
            source = folder / rel
            if source.is_file():
                images.append(str(source))
        revision = int(chapter.get('revision') or 1) + 1
        record = self.artifacts.register(
            stage='pdf2md',
            title=chapter['title'],
            chapter={'index': chapter.get('index'), 'title': chapter.get('title'),
                     'pages': chapter.get('pages') or []},
            source={'jobId': ident, 'pdfName': job.get('fileName'),
                    'pdfSha256': job.get('sha256'), 'pdfPath': job.get('sourcePath'),
                    'editedFrom': artifact['id'], 'revision': revision},
            files={'md': md_path, 'images': images},
            meta={'chars': len(text), 'imageCount': len(images), 'mdName': md_path.name,
                  'edited': True, 'revision': revision},
        )
        chapter.update(artifactId=record['id'], mdName=md_path.name, chars=len(text),
                       revision=revision, edited=True, updated=now())
        job['chapters'] = chapters
        job['updated'] = now()
        job['log'].append(f'保存第 {index} 章修订 r{revision}（{len(text)} 字符）')
        self._save_pdf_job(job)
        return job

    # ---- publishing --------------------------------------------------
    def publish(self, *, source_path, name, kind, type_, origin, assets=None, text=None):
        ident = uuid.uuid4().hex
        folder = self.data / 'files' / ident
        folder.mkdir(parents=True)
        if text is None:
            target = folder / ('source' + Path(source_path).suffix.lower())
            shutil.copy2(source_path, target)
            record = {'id': ident, 'fileId': ident, 'name': name, 'title': name, 'size': target.stat().st_size,
                      'kind': kind, 'sha256': sha256_file(target), 'path': str(target), 'created': now(),
                      'origin': origin}
            record['text'], parsed = parse_document(target, folder)
            record['assets'] = parsed
        else:
            target = folder / 'source.md'
            target.write_text(text, encoding='utf-8')
            record = {'id': ident, 'fileId': ident, 'name': name, 'title': name,
                      'size': target.stat().st_size, 'kind': kind, 'sha256': sha256_file(target),
                      'path': str(target), 'created': now(), 'origin': origin, 'text': text}
            stored = []
            for asset in assets or []:
                source = Path(asset['path'])
                if not source.is_file():
                    continue
                asset_dir = folder / 'assets'
                asset_dir.mkdir(exist_ok=True)
                copy = asset_dir / source.name
                if copy.exists():
                    copy = asset_dir / f'{source.stem}-{len(stored)}{source.suffix}'
                shutil.copy2(source, copy)
                stored.append({'path': str(copy), 'name': asset.get('name') or copy.name,
                               'sha256': sha256_file(copy)})
            record['assets'] = stored
        record['meta'] = f'{len(record["text"])} 字 · {len(record["assets"])} 张原图 · 解析完成'
        write_json(folder / 'file.json', record)
        entry_id = uuid.uuid4().hex
        entry = {'id': entry_id, 'fileId': ident, 'title': name, 'type': type_,
                 'desc': record['text'][:160], 'meta': record['meta'], 'created': now(),
                 'origin': origin}
        write_json(self.data / 'knowledge' / entry_id / 'entry.json', entry)
        return entry

    def _bundle_markdown(self, artifact):
        """Publish an md artifact as a reference whose original images stay visible."""
        folder = Path(artifact['_folder'])
        files = artifact['files']
        md_names = files.get('md') if isinstance(files.get('md'), list) else [files.get('md')]
        texts = [(folder / name).read_text(encoding='utf-8') for name in md_names if name]
        text = '\n\n'.join(texts)
        assets = []
        seen = set()
        for rel in IMAGE_REF.findall(text):
            name = Path(rel).name
            if name in seen:
                continue
            seen.add(name)
            source = folder / 'images' / name
            if source.is_file():
                assets.append({'path': str(source), 'name': name})
        name = artifact['title'].replace('/', '_') + '.md'
        return self.publish(source_path=None, name=name, kind='reference', type_='jiaocai',
                            origin={'artifactId': artifact['id'], 'stage': artifact['stage'],
                                    'sha256': artifact['source'].get('pdfSha256'),
                                    'pdfName': artifact['source'].get('pdfName')},
                            assets=assets, text=text)

    def handoff_to_ppt(self, artifact_id):
        artifact = self.artifacts.get(artifact_id)
        if not artifact:
            raise Problem('阶段产物不存在', 404, 'E_NOT_FOUND')
        artifact['_folder'] = str(self.artifacts.folder(artifact_id))
        published = artifact.get('published') or {}
        refs = []

        def reuse_or_publish(key, publisher, type_):
            entry_id = (published.get(key) or {}).get('entryId')
            if entry_id and (self.data / 'knowledge' / entry_id / 'entry.json').is_file():
                stored = read_json(self.data / 'knowledge' / entry_id / 'entry.json')
                refs.append({'id': stored['id'], 'title': stored['title'], 'type': stored['type']})
                return
            entry = publisher()
            published[key] = {'entryId': entry['id'], 'fileId': entry['fileId'], 'title': entry['title']}
            refs.append({'id': entry['id'], 'title': entry['title'], 'type': entry['type']})

        if artifact['stage'] == 'lesson':
            def pub_lesson():
                docx = self.artifacts.file_path(artifact, 'docx')
                if not docx:
                    raise Problem('该教案产物缺少 DOCX 文件', 409, 'E_ARTIFACT_INCOMPLETE')
                return self.publish(source_path=docx, name=artifact['title'] + '.docx', kind='reference',
                                    type_='jiaoan',
                                    origin={'artifactId': artifact['id'], 'jobId': artifact['source'].get('jobId'),
                                            'sha256': artifact['source'].get('docxSha256'), 'stage': 'lesson'})
            reuse_or_publish('lesson', pub_lesson, 'jiaoan')
            for index, textbook_id in enumerate(artifact['source'].get('textbookArtifacts') or []):
                textbook = self.artifacts.get(textbook_id)
                if not textbook:
                    continue
                textbook['_folder'] = str(self.artifacts.folder(textbook_id))
                reuse_or_publish('textbook-' + textbook_id,
                                 lambda tb=textbook: self._bundle_markdown(tb), 'jiaocai')
            for knowledge_id in artifact['source'].get('knowledgeRefs') or []:
                if any(item['id'] == knowledge_id for item in refs):
                    continue
                entry = read_json(self.data / 'knowledge' / knowledge_id / 'entry.json')
                if entry and not any(item['title'] == entry['title'] and item['type'] == 'jiaocai' for item in refs):
                    refs.append({'id': entry['id'], 'title': entry['title'], 'type': 'jiaocai'})
        else:
            reuse_or_publish('textbook', lambda: self._bundle_markdown(artifact), 'jiaocai')
            for textbook_id in artifact['source'].get('textbookArtifacts') or []:
                textbook = self.artifacts.get(textbook_id)
                if textbook:
                    textbook['_folder'] = str(self.artifacts.folder(textbook_id))
                    reuse_or_publish('textbook-' + textbook_id,
                                     lambda tb=textbook: self._bundle_markdown(tb), 'jiaocai')

        artifact.pop('_folder', None)
        artifact['published'] = published
        self.artifacts.update(artifact)
        topic = str(artifact.get('meta', {}).get('topic') or artifact.get('title') or '').strip()
        return {'refs': refs, 'topic': topic, 'artifactId': artifact_id}


def create_blueprint(data_dir):
    service = PipelineService(data_dir)
    bp = Blueprint('pipeline_api', __name__, url_prefix='/api/pipeline')

    @bp.get('/artifacts')
    def artifacts():
        return jsonify(service.artifacts.list(request.args.get('stage'), limit=60))

    @bp.get('/artifacts/<ident>')
    def artifact_detail(ident):
        record = service.artifacts.get(ident)
        if not record:
            raise Problem('阶段产物不存在', 404, 'E_NOT_FOUND')
        record['downloads'] = list((record.get('files') or {}).keys())
        return jsonify(record)

    @bp.get('/artifacts/<ident>/download/<kind>')
    def artifact_download(ident, kind):
        record = service.artifacts.get(ident)
        if not record:
            raise Problem('阶段产物不存在', 404, 'E_NOT_FOUND')
        path = service.artifacts.file_path(record, kind)
        if not path:
            raise Problem('该产物没有此文件', 404, 'E_NOT_FOUND')
        return send_file(path, as_attachment=True, download_name=path.name)

    @bp.post('/handoff/ppt')
    def handoff():
        body = request.get_json() or {}
        return jsonify(service.handoff_to_ppt(body.get('artifactId')))

    # ---- PDF stage ----------------------------------------------------
    @bp.post('/pdf/jobs')
    def pdf_create():
        upload = request.files.get('file')
        if not upload or not upload.filename:
            raise Problem('请选择 PDF 文件')
        job = service._new_pdf(upload)
        return jsonify(service._pdf_public(job)), 202

    @bp.get('/pdf/jobs')
    def pdf_list():
        jobs = []
        for path in sorted((service.root / 'pdf').glob('*/job.json')):
            job = read_json(path)
            if job:
                jobs.append(service._pdf_public(job))
        jobs.sort(key=lambda item: item.get('created', ''), reverse=True)
        return jsonify(jobs[:40])

    @bp.get('/pdf/jobs/<ident>')
    def pdf_detail(ident):
        job = service._pdf_job(ident)
        if not job:
            raise Problem('任务不存在', 404, 'E_NOT_FOUND')
        return jsonify(service._pdf_public(job))

    @bp.get('/pdf/jobs/<ident>/preview/<int:index>')
    def pdf_preview(ident, index):
        job = service._pdf_job(ident)
        chapters = job.get('chapters') or [] if job else []
        if not job or not 0 < index <= len(chapters):
            raise Problem('章节不存在', 404, 'E_NOT_FOUND')
        artifact = service.artifacts.get(chapters[index - 1].get('artifactId') or '')
        path = service.artifacts.file_path(artifact, 'md') if artifact else None
        if not path:
            raise Problem('Markdown 文件不存在', 404, 'E_NOT_FOUND')
        return send_file(path, mimetype='text/markdown; charset=utf-8')

    @bp.post('/pdf/jobs/<ident>/chapters/<int:index>')
    def pdf_save_chapter(ident, index):
        payload = request.get_json(silent=True) or {}
        markdown = payload.get('markdown')
        if not isinstance(markdown, str):
            raise Problem('缺少 markdown 内容', 400, 'E_INVALID_INPUT')
        job = service.save_pdf_chapter(ident, index, markdown)
        return jsonify(service._pdf_public(job))

    @bp.get('/pdf/jobs/<ident>/images/<int:index>/<path:name>')
    def pdf_image(ident, index, name):
        job = service._pdf_job(ident)
        chapters = job.get('chapters') or [] if job else []
        if not job or not 0 < index <= len(chapters):
            raise Problem('章节不存在', 404, 'E_NOT_FOUND')
        if Path(name).name != name:
            raise Problem('图片不存在', 404, 'E_NOT_FOUND')
        artifact = service.artifacts.get(chapters[index - 1].get('artifactId') or '')
        folder = service.artifacts.folder(artifact['id']) if artifact else None
        path = (folder / 'images' / name).resolve() if folder else None
        if not path or not path.is_file() or not path.is_relative_to(folder.resolve()):
            raise Problem('图片不存在', 404, 'E_NOT_FOUND')
        return send_file(path, conditional=True, max_age=86400)

    @bp.get('/pdf/jobs/<ident>/download/<int:index>')
    def pdf_download(ident, index):
        job = service._pdf_job(ident)
        chapters = job.get('chapters') or [] if job else []
        if not job or not 0 < index <= len(chapters):
            raise Problem('章节不存在', 404, 'E_NOT_FOUND')
        artifact = service.artifacts.get(chapters[index - 1].get('artifactId') or '')
        path = service.artifacts.file_path(artifact, 'md') if artifact else None
        if not path:
            raise Problem('Markdown 文件不存在', 404, 'E_NOT_FOUND')
        return send_file(path, as_attachment=True, download_name=path.name)

    @bp.get('/pdf/jobs/<ident>/images/<int:index>.zip')
    def pdf_images(ident, index):
        job = service._pdf_job(ident)
        chapters = job.get('chapters') or [] if job else []
        if not job or not 0 < index <= len(chapters):
            raise Problem('章节不存在', 404, 'E_NOT_FOUND')
        artifact = service.artifacts.get(chapters[index - 1].get('artifactId') or '')
        if not artifact:
            raise Problem('章节产物不存在', 404, 'E_NOT_FOUND')
        folder = service.artifacts.folder(artifact['id'])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted((folder / 'images').glob('*')):
                if path.is_file():
                    archive.write(path, arcname=f'images/{path.name}')
        buffer.seek(0)
        return send_file(buffer, mimetype='application/zip', as_attachment=True,
                         download_name=f'{artifact["title"]}-images.zip')

    @bp.get('/pdf/jobs/<ident>/log')
    def pdf_log(ident):
        job = service._pdf_job(ident)
        if not job:
            raise Problem('任务不存在', 404, 'E_NOT_FOUND')
        return jsonify(log=job.get('log') or [])

    @bp.post('/pdf/jobs/<ident>/retry')
    def pdf_retry(ident):
        job = service._pdf_job(ident)
        if not job:
            raise Problem('任务不存在', 404, 'E_NOT_FOUND')
        if job.get('status') in ('queued', 'running'):
            raise Problem('任务正在执行', 409, 'E_BUSY')
        if job.get('status') == 'succeeded':
            raise Problem('该任务已完成；如需重新转换请新建任务', 409, 'E_ALREADY_SUCCEEDED')
        source = Path(job.get('sourcePath') or '')
        if not source.is_file():
            raise Problem('原始 PDF 缺失，无法重试', 409, 'E_INPUT_MISSING')
        job.update(status='queued', error=None, code=None, log=['重新执行'], updated=now())
        service._save_pdf_job(job)
        threading.Thread(target=service._run_pdf, args=(ident,), daemon=True).start()
        return jsonify(service._pdf_public(job)), 202

    # ---- lesson stage --------------------------------------------------
    @bp.post('/lesson/models')
    def lesson_models():
        body = request.get_json() or {}
        try:
            models = jweb._fetch_models(str(body.get('base_url') or ''), str(body.get('api_key') or ''))
            return jsonify(models=models)
        except Exception as error:
            raise Problem(f'获取模型失败：{error}', 400, 'E_MODELS')

    @bp.post('/lesson/test')
    def lesson_test():
        body = request.get_json() or {}
        try:
            return jsonify(jweb._test_api(body))
        except Exception as error:
            raise Problem(f'连接失败：{error}', 400, 'E_MODEL_TEST')

    @bp.post('/lesson/jobs')
    def lesson_create():
        raw = request.form.get('payload') or '{}'
        try:
            payload = json.loads(raw)
        except ValueError:
            raise Problem('任务参数不是有效 JSON')
        if not isinstance(payload, dict):
            raise Problem('任务参数无效')
        upload = request.files.get('textbook')
        artifact_ids = payload.get('textbookArtifacts') or []
        knowledge_ids = payload.get('knowledgeRefs') or []
        if not upload and not artifact_ids and not knowledge_ids:
            raise Problem('请选择教材/模板资料，或上传 Markdown')
        folder = service.root / 'lesson' / 'staging' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        source = {'textbookArtifacts': [], 'knowledgeRefs': [], 'textbookName': '',
                  'pdfNames': [], 'pdfSha256': []}
        try:
            chapters = []
            images_dir = folder / 'images'
            images_dir.mkdir()
            kb_templates = []
            kb_names = []
            for knowledge_id in knowledge_ids:
                if not isinstance(knowledge_id, str) or not re.fullmatch(r'[a-f0-9]{32}', knowledge_id):
                    raise Problem('知识库资料 ID 无效')
                entry = read_json(service.data / 'knowledge' / knowledge_id / 'entry.json')
                if not entry:
                    raise Problem('知识库资料不存在', 404, 'E_NOT_FOUND')
                record = read_json(service.data / 'files' / entry['fileId'] / 'file.json')
                if not record:
                    raise Problem('知识库文件不存在', 404, 'E_NOT_FOUND')
                source_path = Path(record.get('path') or '')
                if not source_path.is_file():
                    raise Problem('知识库文件已被移除', 409, 'E_INPUT_MISSING')
                name = record.get('name') or source_path.name
                suffix = Path(name).suffix.lower()
                if suffix in ('.md', '.markdown'):
                    body = record.get('text') or source_path.read_text(encoding='utf-8', errors='replace')
                    for asset in record.get('assets') or []:
                        asset_path = Path(asset.get('path') or '')
                        if not asset_path.is_file():
                            continue
                        asset_name = asset.get('name') or asset_path.name
                        shutil.copy2(asset_path, images_dir / asset_name)
                        body = body.replace(f'assets/{asset_name}', f'images/{asset_name}')
                    chapters.append(body)
                    source['knowledgeRefs'].append(knowledge_id)
                    kb_names.append(entry.get('title') or name)
                elif suffix == '.docx':
                    kb_templates.append(FileStorage(stream=open(source_path, 'rb'), filename=name,
                                                    content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'))
                else:
                    raise Problem('知识库当前仅支持 Markdown 教材或 DOCX 模板')
            if artifact_ids:
                for artifact_id in artifact_ids:
                    artifact = service.artifacts.get(artifact_id)
                    if not artifact or artifact.get('stage') != 'pdf2md':
                        raise Problem('所选的教材产物不存在或阶段不符')
                    folder_art = service.artifacts.folder(artifact_id)
                    artifact['_folder'] = str(folder_art)
                    md_names = artifact['files'].get('md')
                    parts = [(folder_art / name).read_text(encoding='utf-8')
                             for name in ([md_names] if isinstance(md_names, str) else md_names or [])]
                    body = '\n\n'.join(parts)
                    # Keep every referenced original image, renaming only on a
                    # real name collision between different PDF jobs.
                    for rel in IMAGE_REF.findall(body):
                        name = Path(rel).name
                        source_image = folder_art / 'images' / name
                        if not source_image.is_file():
                            continue
                        target = images_dir / name
                        if target.exists() and sha256_file(target) != sha256_file(source_image):
                            name = f'{sha256_file(source_image)[:8]}-{name}'
                            target = images_dir / name
                        if not target.exists():
                            shutil.copy2(source_image, target)
                        body = body.replace(rel, f'images/{name}')
                    chapters.append(body)
                    source['textbookArtifacts'].append(artifact_id)
                    pdf_name = artifact['source'].get('pdfName')
                    if pdf_name and pdf_name not in source['pdfNames']:
                        source['pdfNames'].append(pdf_name)
                    pdf_sha = artifact['source'].get('pdfSha256')
                    if pdf_sha and pdf_sha not in source['pdfSha256']:
                        source['pdfSha256'].append(pdf_sha)
            uploaded_name = ''
            if upload:
                name = Path(upload.filename.replace('\\', '/')).name
                if Path(name).suffix.lower() not in ('.md', '.markdown'):
                    raise Problem('教材须为 .md 文件')
                content = upload.read(60 * 1024**2 + 1)
                if not content or len(content) > 60 * 1024**2:
                    raise Problem('教材文件须在 1 字节至 60MB 之间')
                chapters.append(decode(content))
                uploaded_name = name
            if not chapters:
                raise Problem('没有可用的教材 Markdown；模板不能单独生成教案')
            (folder / 'textbook.md').write_text('\n\n'.join(chapters), encoding='utf-8')
            names = source['pdfNames'] + kb_names + ([uploaded_name] if uploaded_name else [])
            source['textbookName'] = ' + '.join(dict.fromkeys(names)) or '教材'
            templates = [item for item in request.files.getlist('templates') if item.filename] + kb_templates
            images = [item for item in request.files.getlist('images') if item.filename]
            ident = service.lessons.create(
                textbook_path=folder / 'textbook.md', templates=templates, images=images,
                payload=payload, source=source,
            )
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        return jsonify(service.lessons.public(service.lessons.get(ident))), 202

    @bp.get('/lesson/jobs')
    def lesson_list():
        jobs = []
        for path in sorted((service.root / 'lesson').glob('*/job.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            job = service.lessons._read(path)
            if job:
                jobs.append(service.lessons.public(job))
        return jsonify(jobs[:40])

    @bp.get('/lesson/jobs/<ident>')
    def lesson_detail(ident):
        job = service.lessons.get(ident)
        if not job:
            raise Problem('任务不存在', 404, 'E_NOT_FOUND')
        return jsonify(service.lessons.public(job))

    @bp.get('/lesson/jobs/<ident>/docx/<int:index>')
    def lesson_docx(ident, index):
        path = service.lessons.docx_path(ident, index)
        if not path:
            raise Problem('DOCX 不存在或任务未完成', 404, 'E_NOT_FOUND')
        return send_file(path, as_attachment=True, download_name=path.name)

    @bp.get('/lesson/jobs/<ident>/preview/<int:index>')
    def lesson_preview(ident, index):
        path = service.lessons.preview_md(ident, index)
        if not path:
            raise Problem('中间稿不存在', 404, 'E_NOT_FOUND')
        return send_file(path, mimetype='text/markdown; charset=utf-8')

    @bp.get('/lesson/jobs/<ident>/file/<int:index>/<kind>')
    def lesson_file(ident, index, kind):
        job = service.lessons.get(ident)
        results = (job or {}).get('results') or []
        if not 0 < index <= len(results):
            raise Problem('章节不存在', 404, 'E_NOT_FOUND')
        files = results[index - 1].get('files') or {}
        if kind not in ('plan', 'review', 'acceptance', 'image_report', 'template_guide', 'run', 'markdown'):
            raise Problem('文件类型不支持', 400)
        path = Path(str(files.get(kind) or ''))
        if not path.is_file():
            raise Problem('文件不存在', 404, 'E_NOT_FOUND')
        return send_file(path, as_attachment=True, download_name=path.name)

    @bp.get('/lesson/jobs/<ident>/log')
    def lesson_log(ident):
        text = service.lessons.log(ident)
        if text is None:
            raise Problem('日志不存在', 404, 'E_NOT_FOUND')
        if request.args.get('format') == 'text':
            return text, 200, {'Content-Type': 'text/plain; charset=utf-8'}
        return jsonify(log=text.splitlines()[-400:])

    @bp.post('/lesson/jobs/<ident>/retry')
    def lesson_retry(ident):
        body = request.get_json(silent=True) or {}
        api_payload = body.get('api') if isinstance(body.get('api'), dict) else None
        new_id = service.lessons.retry(ident, api_payload)
        return jsonify(service.lessons.public(service.lessons.get(new_id))), 202

    @bp.post('/lesson/jobs/<ident>/artifact/<int:index>')
    def lesson_artifact(ident, index):
        job = service.lessons.get(ident)
        if not job:
            raise Problem('任务不存在', 404, 'E_NOT_FOUND')
        meta = service.lessons.docx_meta(ident, index)
        if not meta:
            raise Problem('DOCX 不存在或任务未完成', 404, 'E_NOT_FOUND')
        if meta['status'] not in ('ok', 'ok_with_warnings'):
            raise Problem('该学时质检未通过，不能作为成功产物进入 PPT 阶段', 409, 'E_QUALITY_FAILED')
        path = meta['path']
        title = meta['title'].replace('_教案', '')
        record = service.artifacts.register(
            stage='lesson', title=title,
            source={'jobId': ident, 'docxSha256': sha256_file(path),
                    'textbookArtifacts': (job.get('source') or {}).get('textbookArtifacts') or [],
                    'knowledgeRefs': (job.get('source') or {}).get('knowledgeRefs') or [],
                    'textbookName': (job.get('source') or {}).get('textbookName') or ''},
            files={'docx': path},
            meta={'topic': title},
        )
        return jsonify(record), 201

    return bp, service