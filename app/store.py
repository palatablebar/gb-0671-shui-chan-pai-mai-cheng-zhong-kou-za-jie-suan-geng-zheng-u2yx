import sqlite3, json, threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from app.errors import BusinessError

_lock=threading.RLock()

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核')")
    # 批次首次落地时的输入快照：与结果分开存放，重跑只读不覆盖，原始证据不会被误删。
    con.execute("CREATE TABLE IF NOT EXISTS run_snapshots (id TEXT PRIMARY KEY, result TEXT NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS run_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, kind TEXT NOT NULL, accepted INTEGER NOT NULL, standard_version TEXT NOT NULL DEFAULT '', standard_digest TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL, created_at TEXT NOT NULL)")
    return con

def save(path, result):
    run_id=result['manifest']['run_id']
    with _lock, connect(path) as con:
        con.execute('INSERT OR IGNORE INTO runs(id,digest,result) VALUES(?,?,?)', (run_id,result['manifest']['sha256'],json.dumps(result,ensure_ascii=False)))
        con.execute('INSERT OR IGNORE INTO run_snapshots(id,result) VALUES(?,?)', (run_id,json.dumps(result,ensure_ascii=False)))
    return get(path,run_id)

LEGACY_STANDARDS={'version':'旧批未记录档期标准','basis':'dealt_at','basis_label':'成交时间',
                  'timezone':'Asia/Shanghai','periods':[]}

def get(path, run_id):
    with connect(path) as con: row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
    result=json.loads(row['result'])
    # 升级前落库的旧批没有档期字段：显式标注而不是伪造标准，也不覆盖库内原始证据。
    if 'standards' not in result:
        result['standards']=dict(LEGACY_STANDARDS)
        result['legacy_run']=True
        balance=result.get('balance') or {}
        balance.setdefault('cross_period',0);balance.setdefault('periods',[])
        result['balance']=balance
    result.setdefault('recalc',{'reason':'initial','source_run_id':None,
                                'standard_version':result['standards'].get('version','')})
    result.update(revision=row['revision'],status=row['status'])
    return result

def snapshot(path, run_id):
    """取批次首次落地时的输入快照；旧库没有快照表时回退到当前结果。"""
    with connect(path) as con:
        row=con.execute('SELECT result FROM run_snapshots WHERE id=?',(run_id,)).fetchone()
        if row: return json.loads(row['result'])
    return get(path,run_id)

def history(path):
    with connect(path) as con:
        return [dict(r) for r in con.execute('SELECT id,revision,status FROM runs ORDER BY rowid DESC LIMIT 100')]

def review(path,run_id,revision):
    with _lock, connect(path) as con:
        cur=con.execute('UPDATE runs SET revision=revision+1,status=? WHERE id=? AND revision=?',('已复核',run_id,revision))
        if cur.rowcount!=1:
            if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
                raise BusinessError('NOT_FOUND','批次不存在',404)
            raise BusinessError('STALE_VERSION','复核版本已更新，请刷新',409)
    return get(path,run_id)


def log_event(path, run_id, kind, accepted, standard=None, detail=None):
    standard=standard or {}
    with _lock, connect(path) as con:
        con.execute('INSERT INTO run_events(run_id,kind,accepted,standard_version,standard_digest,detail,created_at) VALUES(?,?,?,?,?,?,?)',
                    (run_id,kind,1 if accepted else 0,standard.get('version',''),standard.get('digest',''),
                     json.dumps(detail or {},ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
        return con.execute('SELECT last_insert_rowid() AS id').fetchone()['id']


def _local_time(value):
    try:
        return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=8))).isoformat()
    except (ValueError, TypeError):
        return value


def events(path, run_ids=None):
    with connect(path) as con:
        if run_ids:
            marks=','.join('?' for _ in run_ids)
            rows=con.execute(f'SELECT * FROM run_events WHERE run_id IN ({marks}) ORDER BY id',tuple(run_ids)).fetchall()
        else:
            rows=con.execute('SELECT * FROM run_events ORDER BY id DESC LIMIT 50').fetchall()
    return [{**dict(row),'detail':json.loads(row['detail']),'created_at':_local_time(row['created_at'])} for row in rows]
