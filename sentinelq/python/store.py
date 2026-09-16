# Local history store: SQLite on the board. Feeds the timeline and gives an audit trail.
# Everything stays on the device. A cloud sync hook could be added here; deliberately not done.

import os
import sqlite3
import threading
import time

DB_FILE = os.getenv("SQ_DB_FILE", os.path.join("/app" if os.path.isdir("/app") else ".", ".cache", "pema.db"))


class Store:
    def __init__(self, path=DB_FILE):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS sightings (t REAL, object TEXT, ang REAL, dist REAL, conf REAL, position TEXT);
        CREATE TABLE IF NOT EXISTS events   (t REAL, kind TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS actions  (t REAL, source TEXT, action TEXT, verified INTEGER, detail TEXT);
        CREATE INDEX IF NOT EXISTS s_t ON sightings(t);
        """)
        self.db.commit()
        self._last_flush = 0
        self._pending = []

    def sighting(self, obj, ang, dist, conf, position, t=None):
        self._pending.append((t or time.time(), obj, ang, dist, conf, position))
        if time.time() - self._last_flush > 2:
            self.flush()

    def flush(self):
        with self._lock:
            if self._pending:
                self.db.executemany("INSERT INTO sightings VALUES (?,?,?,?,?,?)", self._pending)
                self._pending = []
                self.db.commit()
            self._last_flush = time.time()

    def event(self, kind, detail=""):
        with self._lock:
            self.db.execute("INSERT INTO events VALUES (?,?,?)", (time.time(), kind, str(detail)[:400]))
            self.db.commit()

    def action(self, source, action, verified, detail=""):
        with self._lock:
            self.db.execute("INSERT INTO actions VALUES (?,?,?,?,?)",
                            (time.time(), source, str(action)[:200], 1 if verified else 0, str(detail)[:200]))
            self.db.commit()

    def timeline(self, seconds=1800, step=5):
        """Sightings bucketed for the scrubbable timeline."""
        self.flush()
        since = time.time() - seconds
        with self._lock:
            rows = self.db.execute("SELECT t, object, ang, dist FROM sightings WHERE t > ? ORDER BY t", (since,)).fetchall()
            ev = self.db.execute("SELECT t, kind, detail FROM events WHERE t > ? ORDER BY t", (since,)).fetchall()
            acts = self.db.execute("SELECT t, source, action, verified FROM actions WHERE t > ? ORDER BY t", (since,)).fetchall()
        buckets = {}
        for t, obj, ang, dist in rows:
            b = int(t // step) * step
            buckets.setdefault(b, {})[obj] = [round(ang, 1), round(dist, 2)]
        return {"step": step, "now": time.time(),
                "frames": [{"t": b, "objects": o} for b, o in sorted(buckets.items())],
                "events": [{"t": t, "kind": k, "detail": d} for t, k, d in ev],
                "actions": [{"t": t, "source": s, "action": a, "verified": bool(v)} for t, s, a, v in acts]}

    def counts(self):
        with self._lock:
            s = self.db.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
            e = self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            a = self.db.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
        return {"sightings": s, "events": e, "actions": a, "file": DB_FILE}
