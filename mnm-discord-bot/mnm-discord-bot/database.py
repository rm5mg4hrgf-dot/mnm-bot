"""SQLite persistence layer.

Uses the standard-library ``sqlite3`` module wrapped with ``asyncio.to_thread``
so no extra dependency is needed and the event loop is never blocked. A single
connection is shared and serialised with an ``asyncio.Lock``; WAL mode keeps
writes durable across restarts.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

log = logging.getLogger("mnm.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS attempts(
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    source TEXT NOT NULL,
    correct INTEGER NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE INDEX IF NOT EXISTS idx_attempts_created ON attempts(created_at);

CREATE TABLE IF NOT EXISTS trial_attempts(
    trial_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    submitted_at TEXT,
    score INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (trial_id, user_id)
);

CREATE TABLE IF NOT EXISTS tickets(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER UNIQUE,
    user_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    closed_at TEXT,
    closed_by INTEGER,
    transcript TEXT
);
CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id, status);

CREATE TABLE IF NOT EXISTS mod_actions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    reason TEXT,
    duration_minutes INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mod_user ON mod_actions(user_id, action, active);

CREATE TABLE IF NOT EXISTS filter_words(word TEXT PRIMARY KEY);

CREATE TABLE IF NOT EXISTS role_map(
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (kind, key)
);

CREATE TABLE IF NOT EXISTS showcase(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    discipline TEXT NOT NULL,
    link TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    review_message_id INTEGER,
    published_message_id INTEGER,
    reviewed_by INTEGER,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_showcase_review ON showcase(review_message_id);

CREATE TABLE IF NOT EXISTS opportunities(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT,
    added_by INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_usage(
    day TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id)
);
"""

SCHEMA_VERSION = "1"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ core
    async def connect(self) -> None:
        def _open() -> sqlite3.Connection:
            if str(self.path) != ":memory:":
                self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            conn.commit()
            return conn

        self._conn = await asyncio.to_thread(_open)
        log.info("Database ready at %s", self.path)

    async def close(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)

    async def run(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        """Run ``fn(conn)`` in a worker thread inside one transaction."""
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        conn = self._conn

        def _call() -> Any:
            with conn:  # commit on success, rollback on error
                return fn(conn)

        async with self._lock:
            return await asyncio.to_thread(_call)

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return await self.run(lambda c: c.execute(sql, params).lastrowid)

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        return await self.run(lambda c: c.execute(sql, params).fetchone())

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return await self.run(lambda c: c.execute(sql, params).fetchall())

    async def table_names(self) -> list[str]:
        rows = await self.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [r["name"] for r in rows]

    # -------------------------------------------------------------- attempts
    async def record_attempt(self, user_id: int, key: str, source: str, correct: bool,
                             points: int) -> tuple[bool, int]:
        """Record the first attempt at ``key``. Returns ``(is_first, points_awarded)``.

        The (user_id, key) primary key makes duplicate scoring impossible, even
        with concurrent clicks.
        """
        awarded = points if correct else 0

        def _fn(conn: sqlite3.Connection) -> bool:
            cur = conn.execute(
                "INSERT OR IGNORE INTO attempts(user_id, key, source, correct, points, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (user_id, key, source, 1 if correct else 0, awarded, utcnow_iso()),
            )
            return cur.rowcount == 1

        first = await self.run(_fn)
        return first, (awarded if first else 0)

    async def get_attempt(self, user_id: int, key: str) -> Optional[sqlite3.Row]:
        return await self.fetchone("SELECT * FROM attempts WHERE user_id=? AND key=?", (user_id, key))

    async def attempted_keys(self, user_id: int, prefix: str) -> set[str]:
        rows = await self.fetchall(
            "SELECT key FROM attempts WHERE user_id=? AND substr(key,1,?)=?",
            (user_id, len(prefix), prefix),
        )
        return {r["key"] for r in rows}

    @staticmethod
    def _filters(since: Optional[str], prefix: Optional[str]) -> tuple[str, list[Any]]:
        clauses, params = ["1=1"], []
        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        if prefix:
            clauses.append("substr(key,1,?)=?")
            params.extend([len(prefix), prefix])
        return " AND ".join(clauses), params

    async def leaderboard(self, limit: int = 10, since: Optional[str] = None,
                          prefix: Optional[str] = None) -> list[sqlite3.Row]:
        where, params = self._filters(since, prefix)
        sql = (
            "SELECT user_id, SUM(points) AS total, SUM(correct) AS solved FROM attempts "
            f"WHERE {where} GROUP BY user_id HAVING total > 0 "
            "ORDER BY total DESC, MIN(created_at) ASC LIMIT ?"
        )
        return await self.fetchall(sql, [*params, limit])

    async def user_stats(self, user_id: int, since: Optional[str] = None,
                         prefix: Optional[str] = None) -> dict[str, int]:
        """Return total points, solved count and rank (0 if unranked)."""
        where, params = self._filters(since, prefix)

        def _fn(conn: sqlite3.Connection) -> dict[str, int]:
            mine = conn.execute(
                f"SELECT COALESCE(SUM(points),0) AS total, COALESCE(SUM(correct),0) AS solved, "
                f"COUNT(*) AS attempts FROM attempts WHERE user_id=? AND {where}",
                [user_id, *params],
            ).fetchone()
            total = int(mine["total"])
            rank = 0
            if total > 0:
                better = conn.execute(
                    f"SELECT COUNT(*) AS n FROM (SELECT user_id, SUM(points) AS t FROM attempts "
                    f"WHERE {where} GROUP BY user_id HAVING t > ?)",
                    [*params, total],
                ).fetchone()["n"]
                rank = int(better) + 1
            return {"total": total, "solved": int(mine["solved"]),
                    "attempts": int(mine["attempts"]), "rank": rank}

        return await self.run(_fn)

    # ---------------------------------------------------------------- trials
    async def start_trial(self, trial_id: str, user_id: int) -> sqlite3.Row:
        def _fn(conn: sqlite3.Connection) -> sqlite3.Row:
            conn.execute(
                "INSERT OR IGNORE INTO trial_attempts(trial_id,user_id,started_at) VALUES(?,?,?)",
                (trial_id, user_id, utcnow_iso()),
            )
            return conn.execute(
                "SELECT * FROM trial_attempts WHERE trial_id=? AND user_id=?", (trial_id, user_id)
            ).fetchone()

        return await self.run(_fn)

    async def submit_trial(self, trial_id: str, user_id: int, score: int) -> bool:
        """Mark a trial as submitted. Returns False if it was already submitted."""
        def _fn(conn: sqlite3.Connection) -> bool:
            cur = conn.execute(
                "UPDATE trial_attempts SET submitted_at=?, score=? "
                "WHERE trial_id=? AND user_id=? AND submitted_at IS NULL",
                (utcnow_iso(), score, trial_id, user_id),
            )
            return cur.rowcount == 1

        return await self.run(_fn)

    async def trial_rankings(self, trial_id: str, limit: int = 10) -> list[sqlite3.Row]:
        return await self.fetchall(
            "SELECT user_id, score, "
            "CAST((julianday(submitted_at) - julianday(started_at)) * 86400 AS INTEGER) AS seconds "
            "FROM trial_attempts WHERE trial_id=? AND submitted_at IS NOT NULL AND score > 0 "
            "ORDER BY score DESC, seconds ASC LIMIT ?",
            (trial_id, limit),
        )

    # --------------------------------------------------------------- tickets
    async def create_ticket(self, channel_id: int, user_id: int, category: str) -> int:
        return await self.execute(
            "INSERT INTO tickets(channel_id,user_id,category,created_at) VALUES(?,?,?,?)",
            (channel_id, user_id, category, utcnow_iso()),
        )

    async def open_ticket_for_user(self, user_id: int) -> Optional[sqlite3.Row]:
        return await self.fetchone(
            "SELECT * FROM tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (user_id,),
        )

    async def ticket_by_channel(self, channel_id: int) -> Optional[sqlite3.Row]:
        return await self.fetchone("SELECT * FROM tickets WHERE channel_id=?", (channel_id,))

    async def close_ticket(self, channel_id: int, closed_by: Optional[int], transcript: Optional[str],
                           status: str = "closed") -> bool:
        def _fn(conn: sqlite3.Connection) -> bool:
            cur = conn.execute(
                "UPDATE tickets SET status=?, closed_at=?, closed_by=?, transcript=? "
                "WHERE channel_id=? AND status='open'",
                (status, utcnow_iso(), closed_by, transcript, channel_id),
            )
            return cur.rowcount == 1

        return await self.run(_fn)

    # ------------------------------------------------------------ moderation
    async def add_mod_action(self, user_id: int, moderator_id: int, action: str,
                             reason: Optional[str], duration_minutes: Optional[int] = None) -> int:
        return await self.execute(
            "INSERT INTO mod_actions(user_id,moderator_id,action,reason,duration_minutes,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (user_id, moderator_id, action, reason, duration_minutes, utcnow_iso()),
        )

    async def active_warnings(self, user_id: int) -> list[sqlite3.Row]:
        return await self.fetchall(
            "SELECT * FROM mod_actions WHERE user_id=? AND action='warn' AND active=1 ORDER BY id DESC",
            (user_id,),
        )

    async def recent_actions(self, user_id: int, limit: int = 10) -> list[sqlite3.Row]:
        return await self.fetchall(
            "SELECT * FROM mod_actions WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
        )

    async def clear_warnings(self, user_id: int) -> int:
        def _fn(conn: sqlite3.Connection) -> int:
            return conn.execute(
                "UPDATE mod_actions SET active=0 WHERE user_id=? AND action='warn' AND active=1",
                (user_id,),
            ).rowcount

        return await self.run(_fn)

    # --------------------------------------------------------- filter words
    async def add_filter_word(self, word: str) -> bool:
        def _fn(conn: sqlite3.Connection) -> bool:
            return conn.execute("INSERT OR IGNORE INTO filter_words(word) VALUES(?)", (word,)).rowcount == 1

        return await self.run(_fn)

    async def remove_filter_word(self, word: str) -> bool:
        def _fn(conn: sqlite3.Connection) -> bool:
            return conn.execute("DELETE FROM filter_words WHERE word=?", (word,)).rowcount == 1

        return await self.run(_fn)

    async def filter_words(self) -> list[str]:
        rows = await self.fetchall("SELECT word FROM filter_words ORDER BY word")
        return [r["word"] for r in rows]

    # ------------------------------------------------------------- role map
    async def set_role(self, kind: str, key: str, role_id: int) -> None:
        await self.execute(
            "INSERT INTO role_map(kind,key,role_id) VALUES(?,?,?) "
            "ON CONFLICT(kind,key) DO UPDATE SET role_id=excluded.role_id",
            (kind, key, role_id),
        )

    async def remove_role(self, kind: str, key: str) -> bool:
        def _fn(conn: sqlite3.Connection) -> bool:
            return conn.execute("DELETE FROM role_map WHERE kind=? AND key=?", (kind, key)).rowcount == 1

        return await self.run(_fn)

    async def roles(self, kind: str) -> dict[str, int]:
        rows = await self.fetchall("SELECT key, role_id FROM role_map WHERE kind=?", (kind,))
        return {r["key"]: r["role_id"] for r in rows}

    # ------------------------------------------------------------- showcase
    async def create_showcase(self, user_id: int, title: str, description: str, discipline: str,
                              link: Optional[str]) -> int:
        return await self.execute(
            "INSERT INTO showcase(user_id,title,description,discipline,link,created_at) VALUES(?,?,?,?,?,?)",
            (user_id, title, description, discipline, link, utcnow_iso()),
        )

    async def pending_showcase_count(self, user_id: int) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) AS n FROM showcase WHERE user_id=? AND status='pending'", (user_id,)
        )
        return int(row["n"])

    async def set_showcase_review_message(self, showcase_id: int, message_id: int) -> None:
        await self.execute("UPDATE showcase SET review_message_id=? WHERE id=?", (message_id, showcase_id))

    async def showcase_by_review_message(self, message_id: int) -> Optional[sqlite3.Row]:
        return await self.fetchone("SELECT * FROM showcase WHERE review_message_id=?", (message_id,))

    async def review_showcase(self, showcase_id: int, status: str, reviewer_id: int,
                              published_message_id: Optional[int] = None) -> bool:
        """Atomically move a pending submission to approved/rejected."""
        def _fn(conn: sqlite3.Connection) -> bool:
            cur = conn.execute(
                "UPDATE showcase SET status=?, reviewed_by=?, reviewed_at=?, published_message_id=? "
                "WHERE id=? AND status='pending'",
                (status, reviewer_id, utcnow_iso(), published_message_id, showcase_id),
            )
            return cur.rowcount == 1

        return await self.run(_fn)

    async def set_showcase_published(self, showcase_id: int, message_id: int) -> None:
        await self.execute("UPDATE showcase SET published_message_id=? WHERE id=?", (message_id, showcase_id))

    # --------------------------------------------------------- opportunities
    async def add_opportunity(self, title: str, url: str, category: str, description: Optional[str],
                              added_by: int) -> int:
        return await self.execute(
            "INSERT INTO opportunities(title,url,category,description,added_by,created_at) VALUES(?,?,?,?,?,?)",
            (title, url, category, description, added_by, utcnow_iso()),
        )

    async def remove_opportunity(self, opportunity_id: int) -> bool:
        def _fn(conn: sqlite3.Connection) -> bool:
            return conn.execute("DELETE FROM opportunities WHERE id=?", (opportunity_id,)).rowcount == 1

        return await self.run(_fn)

    async def opportunities(self, limit: int = 25) -> list[sqlite3.Row]:
        return await self.fetchall("SELECT * FROM opportunities ORDER BY category, id DESC LIMIT ?", (limit,))

    # -------------------------------------------------------------- AI usage
    async def ai_try_reserve(self, day: str, user_id: int, user_limit: int, global_limit: int) -> str:
        """Atomically reserve one AI call. Returns 'ok', 'user_limit' or 'global_limit'."""
        def _fn(conn: sqlite3.Connection) -> str:
            row = conn.execute("SELECT COALESCE(SUM(count),0) AS n FROM ai_usage WHERE day=?", (day,)).fetchone()
            if int(row["n"]) >= global_limit:
                return "global_limit"
            mine = conn.execute(
                "SELECT count FROM ai_usage WHERE day=? AND user_id=?", (day, user_id)
            ).fetchone()
            if mine is not None and int(mine["count"]) >= user_limit:
                return "user_limit"
            conn.execute(
                "INSERT INTO ai_usage(day,user_id,count) VALUES(?,?,1) "
                "ON CONFLICT(day,user_id) DO UPDATE SET count=count+1",
                (day, user_id),
            )
            return "ok"

        return await self.run(_fn)

    async def ai_refund(self, day: str, user_id: int) -> None:
        await self.execute(
            "UPDATE ai_usage SET count=MAX(count-1,0) WHERE day=? AND user_id=?", (day, user_id)
        )

    async def ai_usage_today(self, day: str) -> tuple[int, int]:
        row = await self.fetchone(
            "SELECT COALESCE(SUM(count),0) AS n, COUNT(*) AS users FROM ai_usage WHERE day=?", (day,)
        )
        return int(row["n"]), int(row["users"])
