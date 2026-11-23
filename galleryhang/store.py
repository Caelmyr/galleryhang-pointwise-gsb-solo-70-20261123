from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile

from .model import ConflictError, MissingError, digest_token, new_id, timestamp, token


SCHEMA = '''
CREATE TABLE IF NOT EXISTS exhibitions (
    id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    updated TEXT NOT NULL,
    document TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS exhibitions_latest ON exhibitions(updated DESC, id);
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES exhibitions(id),
    layout_id TEXT NOT NULL,
    created TEXT NOT NULL,
    document TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_exhibition ON snapshots(project_id, created DESC);
CREATE TABLE IF NOT EXISTS activity (
    seq INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES exhibitions(id),
    occurred TEXT NOT NULL,
    action TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS activity_by_project ON activity(project_id, seq DESC);
CREATE TABLE IF NOT EXISTS demos (
    name TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES exhibitions(id)
);
'''


def document(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def from_document(row):
    return json.loads(row['document'])


def sha256(bytestring):
    return hashlib.sha256(bytestring).hexdigest()


class BlobStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, digest):
        key = digest_token(digest)
        return self.root / key[:2] / key[2:]

    def put(self, data):
        digest = sha256(data)
        target = self.path(digest)
        if target.is_file():
            return digest
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=target.parent, prefix='image-', delete=False) as temporary:
            name = temporary.name
            try:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary.close()
                os.replace(name, target)
            finally:
                Path(name).unlink(missing_ok=True)
        return digest

    def require(self, digest):
        path = self.path(digest)
        if not path.is_file():
            raise MissingError('The stored image could not be found.')
        return path

    def read(self, digest):
        return self.require(digest).read_bytes()


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.blobs = BlobStore(self.root / 'photos')
        self.database = self.root / 'galleryhang.sqlite3'
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self, *, write=False):
        db = sqlite3.connect(self.database, isolation_level=None, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def lookup(db, identifier):
        key = token(identifier, 'Exhibition')
        row = db.execute('SELECT document FROM exhibitions WHERE id=?', (key,)).fetchone()
        if row is None:
            raise MissingError('Exhibition was not found.')
        return from_document(row)

    @staticmethod
    def insert_event(db, project, action, message):
        db.execute(
            'INSERT INTO activity(project_id, occurred, action, message) VALUES(?,?,?,?)',
            (project, timestamp(), action, message),
        )

    @staticmethod
    def insert_project(db, project, action='create', message='Exhibition created'):
        db.execute(
            'INSERT INTO exhibitions(id, revision, updated, document) VALUES(?,?,?,?)',
            (project['id'], project['revision'], project['updated'], document(project)),
        )
        Store.insert_event(db, project['id'], action, message)
        return project

    def create(self, project):
        with self.connection(write=True) as db:
            return self.insert_project(db, project)

    def get(self, identifier):
        with self.connection() as db:
            return self.lookup(db, identifier)

    def all(self):
        with self.connection() as db:
            projects = db.execute('SELECT document FROM exhibitions ORDER BY updated DESC,id').fetchall()
            return [project_summary(from_document(row)) for row in projects]

    def update(self, identifier, revision, mutation, action):
        with self.connection(write=True) as db:
            project = self.lookup(db, identifier)
            if project['revision'] != revision:
                raise ConflictError('The exhibition changed in another window. Reload it before saving.')
            item, message = mutation(project)
            project['revision'] += 1
            project['updated'] = timestamp()
            db.execute(
                'UPDATE exhibitions SET revision=?, updated=?, document=? WHERE id=?',
                (project['revision'], project['updated'], document(project), identifier),
            )
            self.insert_event(db, identifier, action, message)
            return project, item

    def import_demo(self, name, generate):
        with self.connection() as db:
            record = db.execute('SELECT project_id FROM demos WHERE name=?', (name,)).fetchone()
            if record:
                return self.lookup(db, record['project_id']), False
        candidate = generate()
        with self.connection(write=True) as db:
            record = db.execute('SELECT project_id FROM demos WHERE name=?', (name,)).fetchone()
            if record:
                return self.lookup(db, record['project_id']), False
            self.insert_project(db, candidate, 'demo', 'Synthetic exhibition installed')
            db.execute('INSERT INTO demos(name,project_id) VALUES(?,?)', (name, candidate['id']))
        return candidate, True

    def activity(self, project_id, *, limit=50):
        with self.connection() as db:
            self.lookup(db, project_id)
            rows = db.execute('''SELECT occurred,action,message FROM activity
                                  WHERE project_id=? ORDER BY seq DESC LIMIT ?''', (project_id, limit))
            return [dict(row) for row in rows]

    def save_snapshot(self, project_id, layout_id, revision, build):
        before = self.get(project_id)
        if before['revision'] != revision:
            raise ConflictError('Reload the exhibition to use its latest revision.')
        snapshot = build(before, layout_id)
        snapshot.update(id=new_id(), created=timestamp(), project_id=project_id, layout_id=layout_id)
        with self.connection(write=True) as db:
            current = self.lookup(db, project_id)
            if current['revision'] != revision:
                raise ConflictError('The layout changed while preparing the download. Please retry.')
            existing = db.execute('SELECT COUNT(*) FROM snapshots WHERE project_id=?', (project_id,)).fetchone()[0]
            if existing >= 80:
                raise ConflictError('This exhibition has reached its 80 saved issue limit.')
            db.execute(
                'INSERT INTO snapshots(id,project_id,layout_id,created,document) VALUES(?,?,?,?,?)',
                (snapshot['id'], project_id, layout_id, snapshot['created'], document(snapshot)),
            )
            self.insert_event(db, project_id, 'issue', f"Saved issue {snapshot['name']}")
        return snapshot

    def snapshots(self, project_id):
        with self.connection() as db:
            self.lookup(db, project_id)
            rows = db.execute('''SELECT document FROM snapshots WHERE project_id=?
                                  ORDER BY created DESC,id''', (project_id,))
            return [snapshot_summary(from_document(row)) for row in rows]

    def snapshot(self, project_id, snapshot_id):
        with self.connection() as db:
            row = db.execute('''SELECT document FROM snapshots WHERE project_id=? AND id=?''',
                             (token(project_id), token(snapshot_id))).fetchone()
            if row is None:
                raise MissingError('Saved issue was not found in this exhibition.')
            return from_document(row)


def project_summary(project):
    cover = next((shot['photo']['thumbnail_digest'] for wall in project['walls']
                  for shot in wall['shots']), None)
    return {
        'id':project['id'],
        'name':project['name'],
        'venue':project['venue'],
        'updated':project['updated'],
        'revision':project['revision'],
        'synthetic':project['synthetic'],
        'cover':cover,
        'wall_count':len(project['walls']),
        'artwork_count':len(project['artworks']),
        'layout_count':len(project['layouts']),
    }


def snapshot_summary(snapshot):
    return {key:snapshot[key] for key in (
        'id', 'name', 'created', 'layout_id', 'wall_name', 'layout_name', 'revision', 'artifacts', 'counts',
    )}
