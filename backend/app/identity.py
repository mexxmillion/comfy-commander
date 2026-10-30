"""Controller-owned identity authority. Files are provisioning exports, not authority.

Only explicit initialization imports the old identity. Request-time opens never
create a replacement farm. SQLite transactions make enrollment and revocation
durable across concurrent requests and process crashes.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class IdentityStore:
    def __init__(self, token_file: Path):
        self.token_file = token_file
        self.path = token_file.with_name('identity.sqlite3')

    @contextmanager
    def connection(self):
        # Never create an empty authority when a mount/profile is unavailable.
        with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=rw', uri=True, timeout=15)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous=FULL')
            yield db

    def initialize(self):
        from .auth import service_bridge_token
        marker = self.token_file.with_name('identity.initialized')
        if marker.exists() and not self.path.exists():
            raise RuntimeError('Identity store is missing; restore it, never create another farm identity')
        token = self.token_file.read_text(encoding='ascii').strip()
        if len(token) < 32:
            raise RuntimeError('Existing farm credential is invalid')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            db.execute('PRAGMA synchronous=FULL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS credentials (
                    hash TEXT PRIMARY KEY, role TEXT NOT NULL, subject TEXT NOT NULL,
                    created REAL NOT NULL, revoked REAL);
                CREATE TABLE IF NOT EXISTS sessions (
                    hash TEXT PRIMARY KEY, created REAL NOT NULL, expires REAL NOT NULL,
                    revoked REAL);
            ''')
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute("SELECT value FROM settings WHERE name='bootstrap'").fetchone()
            if not existing:
                bridge_file = self.token_file.with_name('pocket_bridge_token')
                bridge = bridge_file.read_text().strip() if bridge_file.exists() else 'k.' + secrets.token_urlsafe(48)
                values = {'bootstrap': token, 'pocket': bridge, 'installation': secrets.token_hex(16)}
                password = self.token_file.with_name('admin_password.json')
                if password.exists():
                    saved = password.read_text()
                    json.loads(saved)  # Validate, but preserve existing signed-cookie identity.
                    values['password'] = saved
                db.executemany('INSERT INTO settings VALUES (?,?)', values.items())
                for value, role in ((token, 'bootstrap'), (bridge, 'pocket')):
                    db.execute('INSERT INTO credentials VALUES (?,?,?,?,NULL)', (digest(value), role, '', time.time()))
                # Preserve deployed v2 bridges once; future identity is random
                # and is never recalculated from bootstrap/provider credentials.
                db.execute('INSERT OR IGNORE INTO credentials VALUES (?,?,?,?,NULL)',
                           (digest(service_bridge_token(token)), 'pocket', 'legacy-v2', time.time()))
            if not db.execute("SELECT 1 FROM settings WHERE name='pocket_version'").fetchone():
                independent = 'k.' + secrets.token_urlsafe(48)
                db.execute('INSERT INTO credentials VALUES (?,?,?,?,NULL)',
                           (digest(independent), 'pocket', '', time.time()))
                db.execute("UPDATE settings SET value=? WHERE name='pocket'", (independent,))
                db.execute("INSERT INTO settings VALUES ('pocket_version','3')")
        if not marker.exists():
            marker.write_text(self.setting('installation'), encoding='ascii')

    def ready(self):
        if not self.path.exists():
            # One-time migration of a valid installation; no new identity here.
            self.initialize()
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM settings WHERE name='installation'").fetchone():
                raise RuntimeError('Identity authority is incomplete; restore its backup')
        marker = self.token_file.with_name('identity.initialized')
        if not marker.exists():
            marker.write_text(self.setting('installation'), encoding='ascii')
        return self

    def setting(self, name):
        with self.connection() as db:
            row = db.execute('SELECT value FROM settings WHERE name=?', (name,)).fetchone()
        return row['value'] if row else None

    def principal(self, supplied):
        if not supplied:
            return None
        with self.connection() as db:
            row = db.execute('SELECT role,subject FROM credentials WHERE hash=? AND revoked IS NULL',
                             (digest(supplied),)).fetchone()
        return dict(row) if row else None

    def enroll_worker(self, worker_id):
        value = 'w.' + secrets.token_urlsafe(48)
        with self.connection() as db:
            db.execute('INSERT INTO credentials VALUES (?,?,?,?,NULL)',
                       (digest(value), 'worker', worker_id, time.time()))
        return value

    def revoke_worker(self, worker_id):
        with self.connection() as db:
            return db.execute("UPDATE credentials SET revoked=? WHERE role='worker' AND subject=? AND revoked IS NULL",
                              (time.time(), worker_id)).rowcount

    def set_password(self, record, *, initial=False):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if initial and db.execute("SELECT 1 FROM settings WHERE name='password'").fetchone():
                raise ValueError('Password already configured')
            db.execute("INSERT OR REPLACE INTO settings VALUES ('password',?)", (json.dumps(record),))
            db.execute("INSERT OR REPLACE INTO settings VALUES ('legacy_sessions_disabled','1')")
            db.execute('UPDATE sessions SET revoked=? WHERE revoked IS NULL', (time.time(),))

    def new_session(self, lifetime):
        value = 'd.' + secrets.token_urlsafe(48)
        with self.connection() as db:
            db.execute('INSERT INTO sessions VALUES (?,?,?,NULL)',
                       (digest(value), time.time(), time.time() + lifetime))
        return value

    def session_valid(self, value):
        with self.connection() as db:
            return bool(db.execute('SELECT 1 FROM sessions WHERE hash=? AND revoked IS NULL AND expires>?',
                                   (digest(value), time.time())).fetchone())

    def revoke_session(self, value):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?,?)',
                       (digest(value), time.time(), time.time(), time.time()))

    def legacy_revoked(self, value):
        with self.connection() as db:
            if db.execute("SELECT 1 FROM settings WHERE name='legacy_sessions_disabled' AND value='1'").fetchone():
                return True
            return bool(db.execute('SELECT 1 FROM sessions WHERE hash=? AND revoked IS NOT NULL',
                                   (digest(value),)).fetchone())

    def upgrade_session(self, value, lifetime):
        """Exchange an authenticated legacy cookie once, saving its revocation."""
        replacement = 'd.' + secrets.token_urlsafe(48)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO sessions VALUES (?,?,?,NULL)',
                       (digest(replacement), time.time(), time.time() + lifetime))
            db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?,?)',
                       (digest(value), time.time(), time.time(), time.time()))
        return replacement

    def export(self):
        """Repair missing provisioning files from the authority, never replace it."""
        import os
        values = {'api_token': self.setting('bootstrap'), 'pocket_bridge_token': self.setting('pocket'),
                  'admin_password.json': self.setting('password')}
        for name, value in values.items():
            if value is None:
                continue
            path = self.token_file.with_name(name)
            if path.exists() and path.read_text().strip() == value.strip():
                continue
            temporary = path.with_suffix('.export.tmp')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)


def authority(token_file):
    return IdentityStore(token_file).ready()
