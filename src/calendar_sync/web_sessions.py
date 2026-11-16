"""Encrypted ephemeral web records, distinct from control/engine history."""

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import threading
import time

from calendar_sync.control import Vault, private_directory, private_file, _path
from calendar_sync.web_oidc import WebError


class Sessions:
    def __init__(self,path,key_file,*,clock=time.time):
        self.path = _path(path)
        self.vault = Vault(key_file)
        if self.path.parent == self.vault.path.parent:
            raise WebError('web_session_key_must_be_separate',503)
        private_directory(self.path.parent)
        self.lock = threading.RLock()
        self.clock = clock
        self.connection = None
        self.fd = private_file(str(self.path)+'.web-lock',create=True,writable=True)
        try:
            fcntl.flock(self.fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
            if any(os.path.lexists(str(self.path)+suffix) for suffix in ('-wal','-shm','-journal')):
                raise WebError('web_sessions_require_recovery',503)
            os.close(private_file(self.path,create=True,writable=True))
            self.connection = sqlite3.connect(self.path,isolation_level=None,check_same_thread=False)
            self.connection.execute('PRAGMA synchronous=FULL')
            if self.connection.execute('PRAGMA user_version').fetchone()[0]==0 and self.connection.execute(
                    'PRAGMA application_id').fetchone()[0]==0 and not self.connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                self.connection.executescript('''BEGIN IMMEDIATE;
                    CREATE TABLE records(id TEXT PRIMARY KEY, expires REAL NOT NULL, envelope BLOB NOT NULL);
                    CREATE TABLE logout_replays(id TEXT PRIMARY KEY, expires REAL NOT NULL);
                    CREATE TABLE metadata(id TEXT PRIMARY KEY, envelope BLOB NOT NULL);
                    PRAGMA application_id=1129531223; PRAGMA user_version=1; COMMIT;''')
                self.connection.execute('INSERT INTO metadata VALUES (?,?)',
                    ('key',self.vault.seal(b'web-sessions-v1',b'web-key-check')))
            if self.connection.execute('PRAGMA application_id').fetchone()[0]!=1129531223 or self.connection.execute('PRAGMA user_version').fetchone()[0]!=1:
                raise WebError('web_session_schema_invalid',503)
            marker = self.connection.execute("SELECT envelope FROM metadata WHERE id='key'").fetchone()
            if marker is None or self.vault.open(marker[0],b'web-key-check')!=b'web-sessions-v1':
                raise WebError('web_session_key_invalid',503)
        except Exception:
            self.close()
            raise WebError('web_session_storage_unavailable',503) from None

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    @staticmethod
    def digest(token):
        if not isinstance(token,str) or re.fullmatch(r'[A-Za-z0-9_-]{43}',token) is None:
            return None
        return hashlib.sha256(token.encode()).hexdigest()

    def cleanup(self):
        self.connection.execute('DELETE FROM records WHERE expires<=?',(self.clock(),))
        self.connection.execute('DELETE FROM logout_replays WHERE expires<=?',(self.clock(),))

    def put(self,token,value):
        key = self.digest(token)
        if key is None:
            raise WebError('web_session_invalid')
        self.cleanup()
        if not self.connection.execute('SELECT 1 FROM records WHERE id=?',(key,)).fetchone() and self.connection.execute('SELECT count(*) FROM records').fetchone()[0]>=1000:
            raise WebError('web_session_capacity',429)
        expires = float(value['expires'])
        associated = json.dumps([key,expires],separators=(',',':')).encode()
        envelope = self.vault.seal(json.dumps(value,separators=(',',':')).encode(),associated)
        self.connection.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',(key,expires,envelope))

    def get(self,token):
        key = self.digest(token)
        if key is None:
            return None
        row = self.connection.execute('SELECT expires,envelope FROM records WHERE id=?',(key,)).fetchone()
        if row is None:
            return None
        if row[0]<=self.clock():
            self.delete(token)
            return None
        associated = json.dumps([key,row[0]],separators=(',',':')).encode()
        return json.loads(self.vault.open(row[1],associated))

    def delete(self,token):
        self.connection.execute('DELETE FROM records WHERE id=?',(self.digest(token),))

    def backchannel(self,claims):
        self.cleanup()
        key = hashlib.sha256((claims['iss']+'\0'+claims['jti']).encode()).hexdigest()
        if self.connection.execute('SELECT 1 FROM logout_replays WHERE id=?',(key,)).fetchone():
            return
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            self.connection.execute('INSERT INTO logout_replays VALUES (?,?)',(key,claims['iat']+331))
            for identifier,expires,envelope in self.connection.execute('SELECT id,expires,envelope FROM records').fetchall():
                associated = json.dumps([identifier,expires],separators=(',',':')).encode()
                value = json.loads(self.vault.open(envelope,associated))
                if (value.get('kind')=='auth' and value.get('issuer')==claims['iss'] and
                        all(name not in claims or value.get(name)==claims[name] for name in ('sid','sub'))):
                    self.connection.execute('DELETE FROM records WHERE id=?',(identifier,))
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
