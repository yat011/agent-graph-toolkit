"""Read-only SqliteSaver opener. Does not mkdir and does not run SqliteSaver.setup()."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from langgraph.checkpoint.sqlite import SqliteSaver


def readonly_sqlite_uri(path: Path) -> str:
    """SQLite URI for `mode=ro`. Windows paths via Path.as_uri() (POSIX `/`)."""
    return f"{Path(path).resolve().as_uri()}?mode=ro"


@contextmanager
def open_readonly_checkpointer(path: Path) -> Iterator[SqliteSaver]:
    """Yield a SqliteSaver on an existing file without WAL/schema setup writes.

    `is_setup = True` makes `cursor()` skip `PRAGMA journal_mode=WAL` and
    `CREATE TABLE`. `commit()` on a `mode=ro` connection is a no-op.
    """
    conn = sqlite3.connect(
        readonly_sqlite_uri(path),
        uri=True,
        check_same_thread=False,
    )
    try:
        saver = SqliteSaver(conn)
        saver.is_setup = True
        yield saver
    finally:
        conn.close()
