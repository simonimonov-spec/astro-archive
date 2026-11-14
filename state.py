"""Plugin-owned persistent catalogue. No application implementation imports."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, folder):
        self.path = Path(folder) / 'campaigns.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS cache (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS image_sets (
                    id TEXT PRIMARY KEY, campaign TEXT NOT NULL, campaign_name TEXT NOT NULL,
                    team TEXT NOT NULL, team_name TEXT NOT NULL, name TEXT NOT NULL,
                    url TEXT NOT NULL, remote_report TEXT NOT NULL DEFAULT 'unknown',
                    discovered REAL NOT NULL, checked REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new', target TEXT NOT NULL DEFAULT '',
                    manifest TEXT NOT NULL DEFAULT '[]', digest TEXT NOT NULL DEFAULT '',
                    downloaded REAL, local_report INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '', unread INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS sets_team ON image_sets(campaign,team);
            ''')
            if 'site_download' not in {r['name'] for r in db.execute('PRAGMA table_info(image_sets)')}:
                db.execute("ALTER TABLE image_sets ADD COLUMN site_download TEXT NOT NULL DEFAULT 'unknown'")
            for column in ('period','period_end'):
                if column not in {r['name'] for r in db.execute('PRAGMA table_info(image_sets)')}:
                    db.execute('ALTER TABLE image_sets ADD COLUMN '+column+" TEXT NOT NULL DEFAULT ''")
            # An interrupted transfer is retryable; it never means a complete archive.
            db.execute("UPDATE image_sets SET status='interrupted',error='Изтеглянето е прекъснато; може да се повтори.' WHERE status='downloading'")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def cache(self, name, default=None):
        with self.connection() as db:
            row = db.execute('SELECT value FROM cache WHERE name=?', (name,)).fetchone()
        return json.loads(row[0]) if row else default

    def save_cache(self, name, value):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO cache VALUES (?,?)', (name, json.dumps(value, ensure_ascii=False)))

    def merge(self, campaign, team, rows, period=None):
        now, added = time.time(), 0
        start,end=(period or {}).get('start',''),(period or {}).get('end','')
        with self.connection() as db:
            for row in rows:
                key=campaign['id'] + '\0' + team['id'] + '\0'
                ident = hashlib.sha256((key+(start+'\0' if start else '')+row['name']).encode()).hexdigest()
                if start:
                    bound=db.execute('SELECT id FROM image_sets WHERE campaign=? AND team=? AND name=? AND period=?',(campaign['id'],team['id'],row['name'],start)).fetchone()
                    if bound:ident=bound['id']
                    legacy_id=hashlib.sha256((key+row['name']).encode()).hexdigest()
                    legacy=db.execute("SELECT * FROM image_sets WHERE id=? AND period=''",(legacy_id,)).fetchone()
                    if not bound and legacy and legacy['url']==row['url'] and start<=datetime.fromtimestamp(legacy['discovered']).date().isoformat()<=end:
                        ident=legacy_id
                        db.execute('UPDATE image_sets SET period=?,period_end=? WHERE id=?',(start,end,ident))
                existing = db.execute('SELECT id FROM image_sets WHERE id=?', (ident,)).fetchone()
                if existing:
                    db.execute("UPDATE image_sets SET url=?,remote_report=?,site_download=?,checked=?,campaign_name=?,team_name=?,period_end=CASE WHEN ?<>'' THEN ? ELSE period_end END WHERE id=?",
                               (row['url'], row['reported'], row.get('downloaded','unknown'), now, campaign['name'], team['name'],end,end,ident))
                else:
                    db.execute('''INSERT INTO image_sets
                        (id,campaign,campaign_name,team,team_name,name,url,remote_report,site_download,discovered,checked,period,period_end)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                               (ident,campaign['id'],campaign['name'],team['id'],team['name'],row['name'],row['url'],row['reported'],row.get('downloaded','unknown'),now,now,start,end))
                    added += 1
        return added

    def rows(self, slim=False):
        columns = 'id,campaign,campaign_name,team,team_name,name,status,target,local_report,remote_report,site_download,error,unread,period,period_end' if slim else '*'
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT '+columns+' FROM image_sets ORDER BY discovered DESC,name,id')]

    def get(self, ident):
        with self.connection() as db:
            row = db.execute('SELECT * FROM image_sets WHERE id=?', (ident,)).fetchone()
        if not row:
            raise ValueError('Комплектът вече не е в списъка. Обнови изгледа.')
        return dict(row)

    def update(self, ident, **values):
        allowed = {'status','target','manifest','digest','downloaded','error','unread','local_report'}
        if not values or set(values) - allowed:
            raise ValueError('Невалидно обновяване на IASC регистъра.')
        with self.connection() as db:
            db.execute('UPDATE image_sets SET ' + ','.join(k+'=?' for k in values) + ' WHERE id=?', (*values.values(), ident))

    def mark_read(self):
        with self.connection() as db:
            db.execute('UPDATE image_sets SET unread=0')
