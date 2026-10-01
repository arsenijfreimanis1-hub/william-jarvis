import aiosqlite

from jarvis.config import settings

DB_PATH = settings.data_dir / "jarvis.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL DEFAULT 'web',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    detail TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def init_db() -> None:
    from jarvis.services.agent_learning import ensure_tables as ensure_agent_learning_tables
    from jarvis.services.agent_registry import ensure_tables as ensure_agent_tables
    from jarvis.services.event_log import ensure_memory_epoch, ensure_tables as ensure_event_tables
    from jarvis.services.goals import ensure_tables as ensure_goals_tables
    from jarvis.services.build_pipeline import ensure_tables as ensure_build_tables
    from jarvis.services.improve_run import ensure_tables as ensure_improve_run_tables
    from jarvis.services.learning import ensure_tables as ensure_learning_tables
    from jarvis.services.memory import ensure_tables as ensure_memory_tables
    from jarvis.services.screen_observer import ensure_tables as ensure_screen_observer_tables
    from jarvis.services.cursor_trace import ensure_tables as ensure_cursor_trace_tables
    from jarvis.services.people import ensure_people_table
    from jarvis.services.security import ensure_table
    from jarvis.services.sessions import ensure_tables
    from jarvis.services.fleet_registry import ensure_tables as ensure_fleet_tables

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript(SCHEMA)
        for col, typedef in (
            ("parent_id", "INTEGER"),
            ("batch_id", "TEXT"),
            ("priority", "INTEGER NOT NULL DEFAULT 50"),
            ("goal_id", "INTEGER"),
        ):
            try:
                await db.execute(f"ALTER TABLE tasks ADD COLUMN {col} {typedef}")
            except Exception:
                pass
        await db.commit()
    await ensure_tables()
    await ensure_agent_tables()
    await ensure_agent_learning_tables()
    await ensure_goals_tables()
    await ensure_build_tables()
    await ensure_improve_run_tables()
    await ensure_event_tables()
    await ensure_memory_epoch()
    await ensure_learning_tables()
    await ensure_memory_tables()
    await ensure_screen_observer_tables()
    await ensure_cursor_trace_tables()
    await ensure_people_table()
    await ensure_table()
    await ensure_fleet_tables()
    from jarvis.services import compute_fleet

    await compute_fleet.ensure_seed_nodes()
