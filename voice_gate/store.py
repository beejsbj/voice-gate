"""Only exact retained captures persist; partials and full session logs do not."""
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

class CaptureStore:
    def __init__(self,path=':memory:'):
        if path!=':memory:':Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('CREATE TABLE IF NOT EXISTS captures (id TEXT PRIMARY KEY, owner TEXT NOT NULL, session_id TEXT NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL)')
        self.db.commit()
    def add(self,owner,session_id,turn):
        if self.db.execute('SELECT count(*) FROM captures WHERE owner=?',(owner,)).fetchone()[0]>=5000:
            raise ValueError('Capture limit reached; export/delete older captures')
        capture={'id':str(uuid4()),'session_id':session_id,'turn_id':turn['id'],'text':turn['text'],
                 'start_ms':turn['start_ms'],'end_ms':turn['end_ms'],'source':turn['source'],'timing':turn['timing'],'created_at':time.time()}
        self.db.execute('INSERT INTO captures VALUES (?,?,?,?,?)',(capture['id'],owner,session_id,capture['created_at'],json.dumps(capture)))
        self.db.commit();return capture
    def list(self,owner,session_id=None):
        if session_id is None:rows=self.db.execute('SELECT payload FROM captures WHERE owner=? ORDER BY created DESC',(owner,))
        else:rows=self.db.execute('SELECT payload FROM captures WHERE owner=? AND session_id=? ORDER BY created DESC',(owner,session_id))
        return [json.loads(r[0]) for r in rows]
    def delete(self,owner,cid):
        cur=self.db.execute('DELETE FROM captures WHERE owner=? AND id=?',(owner,cid));self.db.commit();return cur.rowcount>0
    def discard(self,owner,sid):
        self.db.execute('DELETE FROM captures WHERE owner=? AND session_id=?',(owner,sid));self.db.commit()
    def close(self):self.db.close()
