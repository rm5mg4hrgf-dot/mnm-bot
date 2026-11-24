"""Create the SQLite database and print its tables.

Usage:  python scripts/setup_database.py
The bot also creates the schema automatically on start-up; this script is for
checking that the database path is writable (e.g. on FadeHost).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from database import Database  # noqa: E402


async def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    raw = os.getenv("DATABASE_PATH", "").strip()
    path = Path(raw) if raw and not raw.upper().startswith(("PASTE_", "YOUR_")) else ROOT / "data" / "runtime" / "mnm.db"
    if not path.is_absolute():
        path = ROOT / path
    db = Database(path)
    await db.connect()
    print(f"Database ready: {path}")
    for name in await db.table_names():
        print(f" - {name}")
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
