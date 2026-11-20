"""Email-code login and per-user data roots.

Everything a user creates lives under ``data/users/<uid>/``; the session cookie
only carries the user id.  Codes are stored hashed and single use.  When SMTP is
not configured the login endpoint returns a clear configuration error instead of
pretending a mail was sent.
"""

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import smtplib
import shutil
import threading
import time
from email.message import EmailMessage

from flask import request, session

EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
CODES = threading.local()
CODE_TTL = 600
CODE_COOLDOWN = 45
MAX_ATTEMPTS = 6


def _read(path, fallback):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return fallback


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def secret(base):
    """Stable signing key so sessions survive a restart without shipping one."""
    value = os.environ.get('AUTH_SECRET')
    if value:
        return value
    path = Path(base) / 'auth' / 'session-secret.txt'
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding='utf-8')
    return path.read_text(encoding='utf-8').strip()


def user_id(email):
    return hashlib.sha256(email.strip().lower().encode('utf-8')).hexdigest()[:24]


def current_uid():
    uid = getattr(CODES, 'uid', None)
    if uid:
        return uid
    try:
        return session.get('uid') or None
    except RuntimeError:  # background worker without a request context
        return None


def set_thread_user(uid):
    CODES.uid = uid


def clear_thread_user():
    CODES.uid = None


class DataRoot:
    """``data / 'tasks'`` resolves to the signed-in user's private tree."""

    def __init__(self, base):
        self.base = Path(base)

    def current(self):
        if os.environ.get('AUTH_DISABLED') == '1':  # test harness: legacy single-tenant tree
            for folder in ('files', 'tasks', 'knowledge'):
                (self.base / folder).mkdir(parents=True, exist_ok=True)
            return self.base
        uid = current_uid()
        root = self.base / 'users' / uid if uid else self.base / 'public'
        for folder in ('files', 'tasks', 'knowledge'):
            (root / folder).mkdir(parents=True, exist_ok=True)
        return root

    def __truediv__(self, other):
        return self.current() / other

    def __fspath__(self):
        return str(self.current())

    def __str__(self):
        return str(self.current())


def user_root(base, uid):
    root = Path(base) / 'users' / uid
    for folder in ('files', 'tasks', 'knowledge'):
        (root / folder).mkdir(parents=True, exist_ok=True)
    return root


def claim_legacy(base, uid):
    """Explicit opt-in: move the pre-login demo tree into the first account."""
    marker = Path(base) / 'auth' / 'legacy-claimed'
    if marker.exists():
        return
    target = Path(base) / 'users' / uid
    for folder in ('files', 'tasks', 'knowledge', 'pipeline', 'artifacts'):
        source, destination = Path(base) / folder, target / folder
        if source.is_dir() and not destination.exists():
            shutil.move(str(source), str(destination))
    stamp_owner(target, uid)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(uid, encoding='utf-8')


def stamp_owner(root, uid):
    """Give freshly imported records an owner so list and detail agree."""
    for name in ('task.json', 'file.json', 'entry.json'):
        for path in Path(root).rglob(name):
            record = _read(path, None)
            if isinstance(record, dict) and not record.get('owner'):
                record['owner'] = uid
                _write(path, record)


def users_path(base):
    return Path(base) / 'users.json'


def ensure_user(base, email):
    email = email.strip().lower()
    users = _read(users_path(base), {})
    record = users.get(email)
    if not record:
        record = {'email': email, 'id': user_id(email), 'created': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        users[email] = record
        _write(users_path(base), users)
    (Path(base) / 'users' / record['id']).mkdir(parents=True, exist_ok=True)
    return record


def find_user(base, email):
    return _read(users_path(base), {}).get(email.strip().lower())


def _code_path(base, email):
    return Path(base) / 'auth' / 'codes' / (user_id(email) + '.json')


def _hash(email, code):
    return hashlib.sha256((email.strip().lower() + ':' + code).encode('utf-8')).hexdigest()


def request_code(base, email):
    email = (email or '').strip().lower()
    if not EMAIL.fullmatch(email):
        raise ValueError('邮箱格式不正确')
    now = time.time()
    path = _code_path(base, email)
    previous = _read(path, {})
    if previous and now - float(previous.get('sent', 0)) < CODE_COOLDOWN:
        raise ValueError(f'请 {CODE_COOLDOWN} 秒后再获取验证码')
    code = f'{secrets.randbelow(10 ** 6):06d}'
    _write(path, {'email': email, 'code': _hash(email, code), 'sent': now,
                  'expires': now + CODE_TTL, 'attempts': 0})
    delivered = send_mail(email, code)
    result = {'sent': True, 'expiresIn': CODE_TTL, 'delivery': 'email' if delivered else 'unconfigured'}
    if not delivered and os.environ.get('AUTH_DEV_CODE') == '1':
        result.update(delivery='dev', devCode=code)
    return result


def send_mail(email, code):
    host = os.environ.get('SMTP_HOST')
    if not host:
        return False
    message = EmailMessage()
    message['Subject'] = '知序工作台登录验证码'
    message['From'] = os.environ.get('SMTP_FROM') or os.environ.get('SMTP_USER') or 'no-reply@localhost'
    message['To'] = email
    message.set_content(f'你的登录验证码是 {code}，{CODE_TTL // 60} 分钟内有效。若非本人操作请忽略。')
    port = int(os.environ.get('SMTP_PORT', '465'))
    user = os.environ.get('SMTP_USER')
    password = os.environ.get('SMTP_PASS')
    if os.environ.get('SMTP_SSL', '1') == '0':
        with smtplib.SMTP(host, port, timeout=20) as client:
            client.starttls()
            if user:
                client.login(user, password or '')
            client.send_message(message)
    else:
        with smtplib.SMTP_SSL(host, port, timeout=20) as client:
            if user:
                client.login(user, password or '')
            client.send_message(message)
    return True


def verify_code(base, email, code):
    email = (email or '').strip().lower()
    code = (code or '').strip()
    if not EMAIL.fullmatch(email) or not re.fullmatch(r'\d{6}', code):
        raise ValueError('邮箱或验证码格式不正确')
    path = _code_path(base, email)
    record = _read(path, {})
    if not record or float(record.get('expires', 0)) < time.time():
        raise ValueError('验证码已过期，请重新获取')
    if int(record.get('attempts', 0)) >= MAX_ATTEMPTS:
        raise ValueError('尝试次数过多，请重新获取验证码')
    if not hmac.compare_digest(record.get('code', ''), _hash(email, code)):
        record['attempts'] = int(record.get('attempts', 0)) + 1
        _write(path, record)
        raise ValueError('验证码不正确')
    path.unlink(missing_ok=True)
    return ensure_user(base, email)


def public_user(record):
    return {'id': record['id'], 'email': record['email']}


def guard(app, base):
    """Reject anonymous API calls; the browser gets 401 and shows the login gate."""
    open_paths = ('/api/auth/', '/api/health', '/static/', '/favicon')

    @app.before_request
    def require_login():
        if os.environ.get('AUTH_DISABLED') == '1':  # explicit test harness only
            return None
        path = request.path
        if not path.startswith('/api/'):
            return None
        if path in ('/api/health',) or path.startswith(open_paths):
            return None
        if not current_uid():
            return {'error': '请先登录', 'code': 'E_UNAUTHORIZED'}, 401
        return None