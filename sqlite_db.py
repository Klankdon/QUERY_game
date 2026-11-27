"""DEV realm -- SQLite backend, permissive by design.

SQLite is the honest starting point: it isolates SQL syntax from engine
pedantry, so a beginner can learn structure. STAGING and PROD then
correct the habits SQLite lets slide.

Real-engine note: the DDL below is SQLite-native. It uses AUTOINCREMENT
and permissive typing. A future live MariaDB or Postgres adapter will
substitute MARIADB_DDL_REAL / POSTGRES_DDL_REAL (see sibling modules).
"""
import os
import sqlite3

from db import EngineAdapter, StrictRules

# ---------------------------------------------------------------- DDL
SQLITE_DDL = """
CREATE TABLE hostile_mobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    hp INTEGER NOT NULL,
    armor_class INTEGER NOT NULL,
    elemental_weakness TEXT NOT NULL,
    position_x INTEGER,
    position_y INTEGER
);
CREATE TABLE conduits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zone TEXT NOT NULL,
    status TEXT NOT NULL,
    voltage INTEGER
);
CREATE TABLE inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    query TEXT NOT NULL,
    mana_cost INTEGER NOT NULL,
    hotbar_slot INTEGER UNIQUE,
    times_cast INTEGER NOT NULL DEFAULT 0,
    dialect_written TEXT NOT NULL DEFAULT 'sqlite',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO hostile_mobs (name, hp, armor_class, elemental_weakness, position_x, position_y) VALUES
    ('Rogue_Daemon_v1', 50, 12, 'LIGHTNING', 8, 4),
    ('Glitch_Crawler',  30,  8, 'FIRE',      3, 7),
    ('Subnet_Stalker',  75, 15, 'COLD',     12, 2);

INSERT INTO conduits (zone, status, voltage) VALUES
    ('sector_7_slums', 'frayed', 220),
    ('industrial_core', 'active', 440);

INSERT INTO inventory (item_name, rarity, slot_type) VALUES
    ('Scrap CRT Deck Mk.I', 'Common', 'HUD'),
    ('Root Kit Fragment A', 'Legendary', 'ARMBAND');

INSERT INTO player_state
    (id, handle, level, hp, max_hp, mana, max_mana, position_x, position_y, zone_tier)
VALUES
    (1, 'operator', 1, 40, 40, 20, 20, 5, 5, 1);
"""

SQLITE_KNOWN_ERRORS = [
    ("interrupted",              "[SPELL BACKFIRED] The incantation drew more than the "
                                 "subnet could carry and collapsed."),
    ("no such table",            "[UNBOUND TABLE] The realm has no table by that name."),
    ("no such column",           "[UNBOUND COLUMN] That column is not inscribed here."),
    ("no such function",         "[UNBOUND VERB] The realm does not know that function."),
    ("syntax error",             "[SYNTAX ERROR] The glyphs do not form a valid pattern."),
    ("ambiguous column name",    "[AMBIGUOUS BINDING] Two sources claim that column. "
                                 "Qualify it."),
    ("misuse of aggregate",      "[MISUSED CHANNEL] Aggregates belong in the yield, "
                                 "not the filter."),
    ("UNIQUE constraint failed", "[COLLISION] A record already holds that key."),
    ("CHECK constraint failed",  "[SEALED] The record violates its own binding."),
]


class SQLiteAdapter(EngineAdapter):
    name, dialect, tier = "DEV", "sqlite", 1
    rules   = StrictRules()              # permissive; matches SQLite
    DDL     = SQLITE_DDL

    ALLOWED_ACTIONS = frozenset(
        getattr(sqlite3, n) for n in (
            "SQLITE_SELECT", "SQLITE_READ", "SQLITE_INSERT",
            "SQLITE_UPDATE", "SQLITE_DELETE", "SQLITE_FUNCTION",
            "SQLITE_TRANSACTION",
        ) if hasattr(sqlite3, n)
    )
    GAME_TABLES   = frozenset({"hostile_mobs", "conduits", "inventory",
                                "player_state", "spellbook"})
    WRITE_TABLES  = GAME_TABLES | {"sqlite_sequence"}
    SCHEMA_TABLES = frozenset({"sqlite_master", "sqlite_schema",
                                "sqlite_temp_master"})
    STEP_BUDGET       = 400
    PROGRESS_INTERVAL = 1000

    _ACTION_NAMES = {}
    for _attr in dir(sqlite3):
        if _attr.startswith("SQLITE_"):
            _val = getattr(sqlite3, _attr, None)
            if isinstance(_val, int) and _attr not in ("SQLITE_OK", "SQLITE_DENY",
                                                        "SQLITE_IGNORE"):
                _ACTION_NAMES.setdefault(_val, _attr[7:])

    def __init__(self, file_path="query_sqlite.db"):
        self.file_path = file_path
        self.conn = None
        self._steps = 0
        self._denied_action = None

    def connect(self, reinitialize=False):
        if reinitialize and os.path.exists(self.file_path):
            try:
                os.remove(self.file_path)
            except OSError as exc:
                print(f"[WARNING] Could not clear {self.file_path}: {exc}")
        self.conn = sqlite3.connect(self.file_path)
        return self.conn

    def init_schema(self, conn):
        conn.executescript(self.DDL)
        conn.commit()

    def post_connect(self, conn):
        self._install_sandbox()

    def _install_sandbox(self):
        self.conn.set_authorizer(self._authorize)
        self.conn.set_progress_handler(self._budget_guard, self.PROGRESS_INTERVAL)

    def _authorize(self, action, arg1, arg2, db_name, source):
        if action not in self.ALLOWED_ACTIONS:
            self._denied_action = action
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ and arg1 is not None:
            if arg1 not in self.GAME_TABLES and arg1 not in self.SCHEMA_TABLES:
                self._denied_action = action
                return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE,
                      sqlite3.SQLITE_DELETE):
            if arg1 is not None and arg1 not in self.WRITE_TABLES:
                self._denied_action = action
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def _budget_guard(self):
        self._steps += 1
        return 1 if self._steps > self.STEP_BUDGET else 0

    def begin_query(self):
        self._steps = 0
        self._denied_action = None

    @property
    def last_denial(self):
        if self._denied_action is None:
            return None
        return self._ACTION_NAMES.get(self._denied_action,
                                      f"op#{self._denied_action}")

    def friendly_error(self, exc):
        if self._denied_action is not None:
            return (f"[SEALED CONDUIT] '{self.last_denial}' is not on your "
                    f"permitted list.")
        text = str(exc).lower()
        for needle, message in SQLITE_KNOWN_ERRORS:
            if needle.lower() in text:
                return message
        return f"[GLYPH REJECTED] {exc}"
