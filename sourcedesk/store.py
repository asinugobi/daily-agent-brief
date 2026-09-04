"""SQLite persistence: feed state, items, and clusters.

Feed state matters as much as the items - ETag and Last-Modified are what turn a
daily job from 118 full downloads into 118 cheap 304s, and the error counters are
how a feed that quietly died gets noticed instead of just returning nothing.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone

UTC = timezone.utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  tier INTEGER NOT NULL,
  access TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'feed',
  outlet TEXT NOT NULL DEFAULT '',
  domain TEXT NOT NULL,
  grp TEXT,
  grp_key TEXT,
  cadence TEXT,
  etag TEXT,
  last_modified TEXT,
  last_fetch TEXT,
  last_status TEXT,
  last_error TEXT,
  consecutive_errors INTEGER NOT NULL DEFAULT 0,
  items_seen INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  feed_id TEXT NOT NULL REFERENCES feeds(id),
  guid TEXT NOT NULL,
  url TEXT,
  canon_url TEXT,
  title TEXT NOT NULL,
  norm_title TEXT NOT NULL,
  summary TEXT,
  published TEXT,
  first_seen TEXT NOT NULL,
  UNIQUE (feed_id, guid)
);
CREATE INDEX IF NOT EXISTS idx_items_canon ON items(canon_url);
CREATE INDEX IF NOT EXISTS idx_items_pub ON items(published);
CREATE INDEX IF NOT EXISTS idx_items_norm ON items(norm_title);

CREATE TABLE IF NOT EXISTS clusters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_item_id INTEGER NOT NULL REFERENCES items(id),
  score REAL NOT NULL DEFAULT 0,
  size INTEGER NOT NULL DEFAULT 1,
  outlets INTEGER NOT NULL DEFAULT 1,
  built_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cluster_members (
  cluster_id INTEGER NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
  item_id INTEGER NOT NULL UNIQUE REFERENCES items(id)
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started TEXT NOT NULL,
  finished TEXT,
  fetched INTEGER DEFAULT 0,
  not_modified INTEGER DEFAULT 0,
  failed INTEGER DEFAULT 0,
  new_items INTEGER DEFAULT 0
);
"""


def now_iso():
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path="sourcedesk.db"):
        self.path = path
        new = not os.path.exists(path)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()
        self.created = new

    def _migrate(self):
        """Add columns introduced after a database was first created."""
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(feeds)")}
        if "kind" not in have:
            self.db.execute("ALTER TABLE feeds ADD COLUMN kind TEXT NOT NULL DEFAULT 'feed'")
        if "outlet" not in have:
            self.db.execute("ALTER TABLE feeds ADD COLUMN outlet TEXT NOT NULL DEFAULT ''")
        if "grp_key" not in have:
            self.db.execute("ALTER TABLE feeds ADD COLUMN grp_key TEXT DEFAULT ''")

    def close(self):
        self.db.close()

    # --- feeds ------------------------------------------------------------
    def sync_feeds(self, feeds):
        """Upsert the source list, preserving fetch state for existing rows."""
        cur = self.db.cursor()
        for f in feeds:
            cur.execute("""
                INSERT INTO feeds (id,name,url,tier,access,kind,outlet,domain,grp,grp_key,cadence)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name, url=excluded.url, tier=excluded.tier,
                  access=excluded.access, kind=excluded.kind, outlet=excluded.outlet,
                  domain=excluded.domain, grp=excluded.grp,
                  grp_key=excluded.grp_key, cadence=excluded.cadence
            """, (f["id"], f["name"], f["url"], f["tier"], f["access"],
                  f.get("kind", "feed"), f.get("outlet", ""), f["domain"], f.get("group", ""),
                  f.get("group_key", ""), f.get("cadence", "")))
        self.db.commit()
        return cur.rowcount

    def feeds(self, access=None, kind=None):
        q, args, where = "SELECT * FROM feeds", [], []
        if access:
            where.append("access IN (%s)" % ",".join("?" * len(access)))
            args += list(access)
        if kind:
            where.append("kind IN (%s)" % ",".join("?" * len(kind)))
            args += list(kind)
        if where:
            q += " WHERE " + " AND ".join(where)
        return self.db.execute(q + " ORDER BY tier, id", tuple(args)).fetchall()

    def update_feed_state(self, feed_id, status, etag=None, last_modified=None,
                          error=None, new_items=0):
        if error:
            self.db.execute("""
                UPDATE feeds SET last_fetch=?, last_status=?, last_error=?,
                  consecutive_errors=consecutive_errors+1 WHERE id=?
            """, (now_iso(), status, str(error)[:400], feed_id))
        else:
            self.db.execute("""
                UPDATE feeds SET last_fetch=?, last_status=?, last_error=NULL,
                  consecutive_errors=0, etag=COALESCE(?,etag),
                  last_modified=COALESCE(?,last_modified),
                  items_seen=items_seen+? WHERE id=?
            """, (now_iso(), status, etag, last_modified, new_items, feed_id))
        self.db.commit()

    # --- items ------------------------------------------------------------
    def add_items(self, feed_id, rows):
        """Insert, ignoring per-feed guid collisions. Returns count inserted."""
        cur = self.db.cursor()
        n = 0
        seen = now_iso()
        for r in rows:
            try:
                cur.execute("""
                    INSERT OR IGNORE INTO items
                      (feed_id,guid,url,canon_url,title,norm_title,summary,published,first_seen)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (feed_id, r["guid"], r["url"], r["canon_url"], r["title"],
                      r["norm_title"], r["summary"],
                      r["published"].isoformat() if r["published"] else None, seen))
                n += cur.rowcount
            except sqlite3.Error:
                continue
        self.db.commit()
        return n

    def items_since(self, iso_cutoff):
        """Items published (or first seen, when undated) after the cutoff."""
        return self.db.execute("""
            SELECT i.*, f.name AS feed_name, f.tier, f.domain, f.access, f.grp, f.outlet
            FROM items i JOIN feeds f ON f.id = i.feed_id
            WHERE COALESCE(i.published, i.first_seen) >= ?
            ORDER BY COALESCE(i.published, i.first_seen) DESC
        """, (iso_cutoff,)).fetchall()

    # --- clusters ---------------------------------------------------------
    def replace_clusters(self, clusters):
        cur = self.db.cursor()
        cur.execute("DELETE FROM cluster_members")
        cur.execute("DELETE FROM clusters")
        built = now_iso()
        for c in clusters:
            cur.execute(
                "INSERT INTO clusters (canonical_item_id,score,size,outlets,built_at)"
                " VALUES (?,?,?,?,?)",
                (c["canonical"], c["score"], len(c["members"]),
                 c.get("outlets", 1), built))
            cid = cur.lastrowid
            cur.executemany(
                "INSERT OR IGNORE INTO cluster_members (cluster_id,item_id) VALUES (?,?)",
                [(cid, m) for m in c["members"]])
        self.db.commit()

    def top_clusters(self, limit, min_outlets=None, max_outlets=None):
        where, args = [], []
        if min_outlets is not None:
            where.append("c.outlets >= ?")
            args.append(min_outlets)
        if max_outlets is not None:
            where.append("c.outlets <= ?")
            args.append(max_outlets)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        args.append(limit)
        return self.db.execute("""
            SELECT c.id, c.score, c.size, c.outlets, i.*, f.name AS feed_name,
                   f.tier, f.domain, f.grp, f.grp_key, f.outlet, f.id AS feed_id
            FROM clusters c
            JOIN items i ON i.id = c.canonical_item_id
            JOIN feeds f ON f.id = i.feed_id
        """ + clause + """
            ORDER BY c.score DESC LIMIT ?
        """, tuple(args)).fetchall()

    def clusters_for_pack(self, limit=400):
        """Canonical item of every cluster, newest-scoring first, for beat routing."""
        return self.db.execute("""
            SELECT c.id AS cluster_id, c.score, c.size, c.outlets, i.*,
                   f.name AS feed_name, f.tier, f.domain, f.grp, f.grp_key,
                   f.outlet, f.access
            FROM clusters c
            JOIN items i ON i.id = c.canonical_item_id
            JOIN feeds f ON f.id = i.feed_id
            ORDER BY c.score DESC LIMIT ?
        """, (limit,)).fetchall()

    def cluster_members(self, cluster_id, exclude_item_id):
        return self.db.execute("""
            SELECT i.*, f.name AS feed_name, f.tier, f.domain, f.outlet
            FROM cluster_members m
            JOIN items i ON i.id = m.item_id
            JOIN feeds f ON f.id = i.feed_id
            WHERE m.cluster_id = ? AND i.id != ?
            ORDER BY f.tier, COALESCE(i.published, i.first_seen)
        """, (cluster_id, exclude_item_id)).fetchall()

    # --- runs -------------------------------------------------------------
    def start_run(self):
        cur = self.db.execute("INSERT INTO runs (started) VALUES (?)", (now_iso(),))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id, **kw):
        self.db.execute("""
            UPDATE runs SET finished=?, fetched=?, not_modified=?, failed=?, new_items=?
            WHERE id=?
        """, (now_iso(), kw.get("fetched", 0), kw.get("not_modified", 0),
              kw.get("failed", 0), kw.get("new_items", 0), run_id))
        self.db.commit()

    def health(self):
        return self.db.execute("""
            SELECT id, name, tier, access, kind, last_status, consecutive_errors,
                   items_seen, last_error
            FROM feeds ORDER BY consecutive_errors DESC, tier, id
        """).fetchall()
