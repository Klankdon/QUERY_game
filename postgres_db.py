"""PROD realm -- PostgreSQL semantics, simulated on a SQLite backend.

Postgres defaults enforced pre-execution:
  * Only columns in GROUP BY (or aggregates) may appear in SELECT
  * Strict typing on INSERT
  * `||` is concatenation here -- consistently; the trap is the OTHER way
    (spells written in MariaDB that use `||` as OR will silently become
    concatenation in real Postgres)

POSTGRES_DDL_REAL documents the physical schema this realm would use in a
live deployment. v0.5 uses SQLITE_DDL (inherited).
"""
from db import StrictRules
from sqlite_db import SQLiteAdapter, SQLITE_DDL

POSTGRES_DDL_REAL = """
CREATE TABLE hostile_mobs (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    hp INTEGER NOT NULL,
    armor_class INTEGER NOT NULL,
    elemental_weakness TEXT NOT NULL,
    position_x INTEGER,
    position_y INTEGER
);

CREATE TABLE conduits (
    id SERIAL PRIMARY KEY,
    zone TEXT NOT NULL,
    status TEXT NOT NULL,
    voltage INTEGER
);

CREATE TABLE inventory (
    id SERIAL PRIMARY KEY,
    item_name TEXT NOT NULL,
    rarity TEXT NOT NULL,
    slot_type TEXT NOT NULL
);

CREATE TABLE player_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    handle TEXT NOT NULL,
    level INTEGER NOT NULL DEFAULT 1,
    hp INTEGER NOT NULL,
    max_hp INTEGER NOT NULL,
    mana INTEGER NOT NULL,
    max_mana INTEGER NOT NULL,
    position_x INTEGER NOT NULL,
    position_y INTEGER NOT NULL,
    zone_tier INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE spellbook (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    query TEXT NOT NULL,
    mana_cost INTEGER NOT NULL,
    hotbar_slot INTEGER UNIQUE,
    times_cast INTEGER NOT NULL DEFAULT 0,
    dialect_written TEXT NOT NULL DEFAULT 'postgres',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class PostgresAdapter(SQLiteAdapter):
    name, dialect, tier = "PROD", "postgres", 3
    rules = StrictRules(only_full_group_by=True, strict_types=True)
    DDL   = SQLITE_DDL        # simulated; live swap -> POSTGRES_DDL_REAL

    def __init__(self, file_path="query_postgres.db"):
        super().__init__(file_path)
