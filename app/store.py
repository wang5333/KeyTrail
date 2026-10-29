import json
from pathlib import Path
import sqlite3
import threading

from .core import atomic_json, make_config, now, partition, profile_id, validate_config, config_range


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / "progress.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS progress (
            profile TEXT PRIMARY KEY, start_hex TEXT, end_hex TEXT, next_hex TEXT,
            checked TEXT NOT NULL, runtime REAL NOT NULL DEFAULT 0, updated REAL);
          CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, kind TEXT, message TEXT);
          CREATE TABLE IF NOT EXISTS plans (profile TEXT PRIMARY KEY, config TEXT NOT NULL);
        """)
        path = self.directory / "config.json"
        if path.exists():
            self.config = validate_config(json.loads(path.read_text(encoding="utf8")))
        else:
            self.config = make_config()
            atomic_json(path, self.config)
        self.ensure_profile()

    def ensure_profile(self, config=None):
        config = config or self.config
        start, end = partition(config["total"], config["index"], *config_range(config))
        with self.lock:
            existing = self.db.execute("SELECT start_hex,end_hex FROM progress WHERE profile=?",(profile_id(config),)).fetchone()
            if existing and (int(existing[0],16),int(existing[1],16)) != (start,end):
                raise ValueError("Existing plan has different bounds; create a new plan")
            self.db.execute("INSERT OR IGNORE INTO progress VALUES (?,?,?,?,?,?,?)",
                            (profile_id(config), hex(start), hex(end), hex(start), "0", 0, now()))
            self.db.execute("INSERT OR REPLACE INTO plans VALUES (?,?)",(profile_id(config),json.dumps(config)))
            self.db.commit()

    def change_config(self, config):
        config = validate_config(config)
        with self.lock:
            self.ensure_profile(config)
            atomic_json(self.directory / "config.json", config)
            self.config = config
        self.event("info", f"已导入电脑 {config['index']}/{config['total']} 的独立区间")

    def progress(self):
        with self.lock:
            row = self.db.execute("SELECT * FROM progress WHERE profile=?",
                                  (profile_id(self.config),)).fetchone()
            return dict(zip(("profile", "start_hex", "end_hex", "next_hex", "checked", "runtime", "updated"), row))

    def commit(self, next_key, seconds=0):
        with self.lock:
            row = self.progress()
            current, start, end = int(row["next_hex"], 16), int(row["start_hex"], 16), int(row["end_hex"], 16)
            if not current <= next_key <= end + 1:
                raise ValueError("进度越界或倒退")
            self.db.execute("UPDATE progress SET next_hex=?,checked=?,runtime=runtime+?,updated=? WHERE profile=?",
                            (hex(next_key), str(next_key - start), max(0, seconds), now(), row["profile"]))
            self.db.commit()

    def event(self, kind, message):
        with self.lock:
            self.db.execute("INSERT INTO events(at,kind,message) VALUES(?,?,?)",
                            (now(), str(kind)[:30], str(message)[:1000]))
            self.db.execute("DELETE FROM events WHERE id < (SELECT COALESCE(MAX(id),0)-1000 FROM events)")
            self.db.commit()

    def events(self, limit=60):
        with self.lock:
            rows = self.db.execute("SELECT id,at,kind,message FROM events ORDER BY id DESC LIMIT ?",
                                   (min(int(limit), 200),)).fetchall()
            return [dict(zip(("id", "at", "kind", "message"), r)) for r in rows]

    def total_checked(self):
        with self.lock:
            rows=self.db.execute("SELECT start_hex,next_hex FROM progress").fetchall()
        ranges=sorted((int(a,16),int(b,16)) for a,b in rows if int(b,16)>int(a,16))
        total=0
        if ranges:
            begin,end=ranges[0]
            for a,b in ranges[1:]:
                if a<=end:
                    end=max(end,b)
                else:
                    total+=end-begin
                    begin,end=a,b
            total+=end-begin
        return str(total)

    def saved_plans(self):
        with self.lock:
            rows=self.db.execute("SELECT p.config,r.checked,r.next_hex FROM plans p JOIN progress r ON p.profile=r.profile ORDER BY r.updated DESC LIMIT 25").fetchall()
        return [{"config":json.loads(c),"checked":n,"next_hex":h} for c,n,h in rows]
