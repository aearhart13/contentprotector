import json
import sqlite3
from config import DATABASE_PATH

# ── Default client seed data ───────────────────────────────────────────────────
_DEFAULT_CLIENTS = [
    {
        "id": 1,
        "slug": "ryan-hall",
        "name": "Ryan Hall, Y'all",
        "channel_id": "UCJHAT3Uvv-g3I8H3GhHWV7w",
        "handle": "@RyanHallYall",
        "keywords_json": json.dumps([
            "ryan hall", "y'all", "yall", "weather", "storm", "tornado", "chase"
        ]),
        "name_variants_json": json.dumps([
            "Ryan Hall, Y'all", "Ryan Hall Y'all", "Ryan Hall Yall",
            "RyanHallYall", "Ryan Hall",
        ]),
        "search_queries_json": json.dumps([
            "Ryan Hall Y'all",
            "Ryan Hall Yall weather",
            "Ryan Hall storm chase",
            '"Ryan Hall" weather channel',
            "Ryan Hall tornado",
        ]),
        "content_keywords_json": json.dumps([
            "storm", "tornado", "hurricane", "severe", "weather", "chase",
            "lightning", "hail", "flood", "wind", "rain", "forecast", "warning",
            "watch", "outbreak", "supercell", "funnel", "damage", "thunderstorm",
            "derecho", "blizzard", "atmospheric", "meteorolog",
        ]),
    },
    {
        "id": 2,
        "slug": "heather-cox-richardson",
        "name": "Heather Cox Richardson",
        "channel_id": "UCnbKOlm6H9njgmN-Yil90Rg",
        "handle": "@heathercoxrichardson",
        "keywords_json": json.dumps([
            "heather cox richardson", "heather cox", "heather richardson",
            "letters from an american", "hcr",
        ]),
        "name_variants_json": json.dumps([
            "Heather Cox Richardson",
            "Heather Richardson",
            "HeatherCoxRichardson",
            "Heather Cox",
            "Letters from an American",
        ]),
        "search_queries_json": json.dumps([
            "Heather Cox Richardson",
            "@heathercoxrichardson",
            '"Letters from an American"',
            "Heather Richardson history politics",
        ]),
        "content_keywords_json": json.dumps([
            "democracy", "history", "republican", "democratic", "congress",
            "senate", "election", "political", "constitution", "amendment",
            "vote", "legislation", "america", "freedom", "rights", "letter",
            "newsletter", "heather", "richardson", "substack",
        ]),
    },
]


def get_conn():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_cols(conn, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def init_db():
    with get_conn() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        # Step 1: rename old single-client channels/feedback/model_runs if needed
        if "channels" in tables:
            cols = _table_cols(conn, "channels")
            if "client_id" not in cols:
                conn.executescript("""
                    ALTER TABLE channels   RENAME TO channels_old;
                    ALTER TABLE feedback   RENAME TO feedback_old;
                    ALTER TABLE model_runs RENAME TO model_runs_old;
                """)
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}

        # Step 2: ensure videos table has client_id (may have been created without it)
        if "videos" in tables and "client_id" not in _table_cols(conn, "videos"):
            conn.execute("ALTER TABLE videos ADD COLUMN client_id INTEGER NOT NULL DEFAULT 1")

        # Step 3: add takedown tracking columns if missing
        if "channels" in tables:
            cols = _table_cols(conn, "channels")
            if "taken_down" not in cols:
                conn.execute("ALTER TABLE channels ADD COLUMN taken_down INTEGER DEFAULT 0")
            if "taken_down_at" not in cols:
                conn.execute("ALTER TABLE channels ADD COLUMN taken_down_at TEXT")

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                channel_id TEXT DEFAULT '',
                handle TEXT DEFAULT '',
                keywords_json TEXT DEFAULT '[]',
                name_variants_json TEXT DEFAULT '[]',
                search_queries_json TEXT DEFAULT '[]',
                content_keywords_json TEXT DEFAULT '[]',
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL DEFAULT 1,
                channel_id TEXT NOT NULL,
                video_id TEXT UNIQUE NOT NULL,
                title TEXT,
                description TEXT,
                thumbnail_url TEXT,
                published_at TEXT,
                is_reference INTEGER DEFAULT 0,
                scraped_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_videos_channel    ON videos(channel_id);
            CREATE INDEX IF NOT EXISTS idx_videos_client_ref ON videos(client_id, is_reference);

            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL DEFAULT 1,
                channel_id TEXT NOT NULL,
                channel_name TEXT,
                description TEXT,
                thumbnail_url TEXT,
                subscriber_count INTEGER,
                video_count INTEGER,
                published_at TEXT,
                heuristic_score REAL,
                ml_score REAL,
                features_json TEXT,
                is_flagged INTEGER DEFAULT 0,
                reviewed INTEGER DEFAULT 0,
                reported_to_youtube INTEGER DEFAULT 0,
                reported_at TEXT,
                taken_down INTEGER DEFAULT 0,
                taken_down_at TEXT,
                scraped_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(client_id, channel_id)
            );
            CREATE INDEX IF NOT EXISTS idx_channels_client ON channels(client_id);

            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                client_id INTEGER NOT NULL DEFAULT 1,
                label INTEGER NOT NULL,
                feedback_type TEXT,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS model_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL DEFAULT 1,
                trained_at TEXT DEFAULT CURRENT_TIMESTAMP,
                num_samples INTEGER,
                num_positives INTEGER,
                accuracy REAL,
                precision_score REAL,
                recall_score REAL,
                f1_score REAL,
                model_type TEXT
            );
        """)

        # Seed default clients (idempotent)
        for c in _DEFAULT_CLIENTS:
            conn.execute("""
                INSERT OR IGNORE INTO clients
                  (id, slug, name, channel_id, handle,
                   keywords_json, name_variants_json, search_queries_json, content_keywords_json)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, [c["id"], c["slug"], c["name"], c["channel_id"], c["handle"],
                  c["keywords_json"], c["name_variants_json"],
                  c["search_queries_json"], c["content_keywords_json"]])

        # Copy data from old tables (if they exist) into the new schema
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        if "channels_old" in existing:
            conn.executescript("""
                INSERT OR IGNORE INTO channels
                  (client_id, channel_id, channel_name, description, thumbnail_url,
                   subscriber_count, video_count, published_at,
                   heuristic_score, ml_score, features_json,
                   is_flagged, reviewed, reported_to_youtube, reported_at, scraped_at)
                SELECT 1, channel_id, channel_name, description, thumbnail_url,
                       subscriber_count, video_count, published_at,
                       heuristic_score, ml_score, features_json,
                       is_flagged, reviewed,
                       COALESCE(reported_to_youtube, 0),
                       reported_at,
                       COALESCE(scraped_at, CURRENT_TIMESTAMP)
                FROM channels_old;

                INSERT OR IGNORE INTO feedback (client_id, channel_id, label, feedback_type, notes, created_at)
                SELECT 1, channel_id, label, feedback_type, notes, created_at FROM feedback_old;

                INSERT OR IGNORE INTO model_runs
                  (client_id, trained_at, num_samples, num_positives,
                   accuracy, precision_score, recall_score, f1_score, model_type)
                SELECT 1, trained_at, num_samples, num_positives,
                       accuracy, precision_score, recall_score, f1_score, model_type
                FROM model_runs_old;

                DROP TABLE channels_old;
                DROP TABLE feedback_old;
                DROP TABLE model_runs_old;
            """)


# ── Client helpers ─────────────────────────────────────────────────────────────

def get_all_clients() -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM clients WHERE active=1 ORDER BY id"
        ).fetchall()]


def get_client(client_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM clients WHERE id=?", [client_id]).fetchone()
        return dict(row) if row else None


def insert_client(data: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO clients
              (slug, name, channel_id, handle,
               keywords_json, name_variants_json, search_queries_json, content_keywords_json)
            VALUES (?,?,?,?,?,?,?,?)
        """, [
            data["slug"], data["name"],
            data.get("channel_id", ""), data.get("handle", ""),
            json.dumps(data.get("keywords", [])),
            json.dumps(data.get("name_variants", [])),
            json.dumps(data.get("search_queries", [])),
            json.dumps(data.get("content_keywords", [])),
        ])
        return cur.lastrowid


def build_client_config(client: dict) -> dict:
    """Expand JSON fields into lists, returning a runtime config dict."""
    return {
        "id":               client["id"],
        "name":             client["name"],
        "slug":             client["slug"],
        "channel_id":       client.get("channel_id") or "",
        "handle":           client.get("handle") or "",
        "keywords":         json.loads(client.get("keywords_json") or "[]"),
        "name_variants":    json.loads(client.get("name_variants_json") or "[]"),
        "search_queries":   json.loads(client.get("search_queries_json") or "[]"),
        "content_keywords": json.loads(client.get("content_keywords_json") or "[]"),
    }


# ── Channel helpers ────────────────────────────────────────────────────────────

def upsert_channel(client_id: int, data: dict):
    data = dict(data)
    data["client_id"] = client_id
    cols         = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    updates      = ", ".join(
        f"{k}=excluded.{k}" for k in data if k not in ("client_id", "channel_id")
    )
    sql = (f"INSERT INTO channels ({cols}) VALUES ({placeholders}) "
           f"ON CONFLICT(client_id, channel_id) DO UPDATE SET {updates}")
    with get_conn() as conn:
        conn.execute(sql, list(data.values()))


def get_channels(client_id: int, flagged_only=False, reviewed=None, limit=100, offset=0):
    sql    = "SELECT * FROM channels WHERE client_id=?"
    params = [client_id]
    if flagged_only:
        sql += " AND is_flagged=1"
    if reviewed is not None:
        sql += " AND reviewed=?"
        params.append(int(reviewed))
    sql += " ORDER BY COALESCE(ml_score, heuristic_score) DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_channel(client_id: int, channel_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM channels WHERE client_id=? AND channel_id=?",
            [client_id, channel_id],
        ).fetchone()
        return dict(row) if row else None


def add_feedback(client_id: int, channel_id: str, label: int, feedback_type: str, notes: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO feedback (client_id, channel_id, label, feedback_type, notes) VALUES (?,?,?,?,?)",
            [client_id, channel_id, label, feedback_type, notes],
        )
        conn.execute(
            "UPDATE channels SET reviewed=1 WHERE client_id=? AND channel_id=?",
            [client_id, channel_id],
        )


def get_labeled_data(client_id: int) -> list:
    sql = """
        SELECT c.features_json, f.label
        FROM feedback f
        JOIN channels c ON c.channel_id = f.channel_id AND c.client_id = f.client_id
        WHERE f.client_id=? AND c.features_json IS NOT NULL
        ORDER BY f.created_at DESC
    """
    with get_conn() as conn:
        rows = conn.execute(sql, [client_id]).fetchall()
    result = []
    for row in rows:
        try:
            result.append((json.loads(row["features_json"]), row["label"]))
        except Exception:
            pass
    return result


def get_reported_active(client_id: int) -> list:
    """Return reported channels not yet confirmed taken down."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT channel_id FROM channels "
            "WHERE client_id=? AND reported_to_youtube=1 AND taken_down=0",
            [client_id],
        ).fetchall()]


def mark_taken_down(client_id: int, channel_ids: list):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        for cid in channel_ids:
            conn.execute(
                "UPDATE channels SET taken_down=1, taken_down_at=? "
                "WHERE client_id=? AND channel_id=?",
                [now, client_id, cid],
            )


def mark_reported(client_id: int, channel_ids: list):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        for cid in channel_ids:
            conn.execute(
                "UPDATE channels SET reported_to_youtube=1, reported_at=? "
                "WHERE client_id=? AND channel_id=?",
                [now, client_id, cid],
            )


def save_model_run(client_id: int, stats: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO model_runs
              (client_id, num_samples, num_positives, accuracy,
               precision_score, recall_score, f1_score, model_type)
            VALUES (:client_id, :num_samples, :num_positives, :accuracy,
                    :precision_score, :recall_score, :f1_score, :model_type)
        """, {**stats, "client_id": client_id})


def get_stats(client_id: int) -> dict:
    with get_conn() as conn:
        def count(q, *p): return conn.execute(q, list(p)).fetchone()[0]
        total    = count("SELECT COUNT(*) FROM channels WHERE client_id=?", client_id)
        flagged  = count("SELECT COUNT(*) FROM channels WHERE client_id=? AND is_flagged=1", client_id)
        reviewed = count("SELECT COUNT(*) FROM channels WHERE client_id=? AND reviewed=1", client_id)
        confirmed= count("SELECT COUNT(*) FROM feedback WHERE client_id=? AND label=1", client_id)
        false_pos= count("SELECT COUNT(*) FROM feedback WHERE client_id=? AND label=0", client_id)
        pending  = count("SELECT COUNT(*) FROM channels WHERE client_id=? AND is_flagged=1 AND reviewed=0", client_id)
        reported   = count("SELECT COUNT(*) FROM channels WHERE client_id=? AND reported_to_youtube=1", client_id)
        taken_down = count("SELECT COUNT(*) FROM channels WHERE client_id=? AND taken_down=1", client_id)
        last_model = conn.execute(
            "SELECT * FROM model_runs WHERE client_id=? ORDER BY trained_at DESC LIMIT 1",
            [client_id],
        ).fetchone()
    return {
        "total_channels":       total,
        "flagged":              flagged,
        "reviewed":             reviewed,
        "confirmed_impersonators": confirmed,
        "false_positives":      false_pos,
        "pending_review":       pending,
        "reported_to_youtube":  reported,
        "taken_down":           taken_down,
        "last_model":           dict(last_model) if last_model else None,
    }


# ── Video helpers ──────────────────────────────────────────────────────────────

def upsert_videos(videos: list):
    if not videos:
        return
    with get_conn() as conn:
        for v in videos:
            cols         = ", ".join(v.keys())
            placeholders = ", ".join(["?"] * len(v))
            updates      = ", ".join(f"{k}=excluded.{k}" for k in v if k != "video_id")
            conn.execute(
                f"INSERT INTO videos ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT(video_id) DO UPDATE SET {updates}",
                list(v.values()),
            )


def get_reference_videos(client_id: int) -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM videos WHERE client_id=? AND is_reference=1 ORDER BY published_at DESC",
            [client_id],
        ).fetchall()]


def get_channel_videos(channel_id: str) -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM videos WHERE channel_id=? AND is_reference=0 ORDER BY published_at DESC",
            [channel_id],
        ).fetchall()]


def reference_videos_stale(client_id: int, refresh_days: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(scraped_at) FROM videos WHERE client_id=? AND is_reference=1",
            [client_id],
        ).fetchone()
    if not row or not row[0]:
        return True
    from datetime import datetime, timezone, timedelta
    last = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last > timedelta(days=refresh_days)
