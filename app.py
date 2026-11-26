"""
QUERY: The Query That Saved The World -- engine v0.3
Realm tiers: DEV (SQLite) -> STAGING (MariaDB) -> PROD (PostgreSQL)

    pip install pygame sqlglot

Changes from v0.2:
  * CONSTRUCT_GATES/CONSTRUCT_PROBES/CONSTRUCT_HINTS merged into CONSTRUCTS
  * EngineAdapter interface; DevAdapter holds all sqlite3 specifics
  * Tier gate: constructs not available in the current realm are rejected
    with [REALM MISMATCH] rather than a syntax error
  * Migration stub: MIGRATE command + level-gated announcement
"""
import os
import random
import sys

import pygame
import sqlite3

try:
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import ParseError
except ImportError:
    sys.exit("Missing dependency: sqlglot\n    pip install sqlglot")

# ---------------------------------------------------------------- display
SCREEN_WIDTH, SCREEN_HEIGHT, FPS = 1280, 720, 60
GRID_SIZE, GRID_ORIGIN_X, GRID_ORIGIN_Y = 40, 40, 40
GRID_COLS, GRID_ROWS = 16, 12

COLOR_BG     = (10, 12, 10)
COLOR_GRID   = (20, 35, 20)
COLOR_TEXT   = (50, 255, 50)
COLOR_MUTED  = (30, 100, 30)
COLOR_ACCENT = (0, 255, 200)
COLOR_DANGER = (255, 50, 50)
COLOR_WARN   = (255, 190, 60)
COLOR_PANEL  = (15, 20, 15)

MANA_PER_STEP = 2
TIER_NAMES = {1: "DEV", 2: "STAGING", 3: "PROD"}
MIGRATION_UNLOCK_LEVEL = 5

# ------------------------------------------------------- curriculum gates
# level  -> player level required to form the pattern
# tiers  -> realms whose engine can actually parse it
# probes -> sqlglot node types (or a predicate) that mark its presence

def _distinct_on(node):
    return isinstance(node, exp.Distinct) and node.args.get("on") is not None

def _json_ops(node):
    return isinstance(node, (exp.JSONExtract, exp.JSONExtractScalar))

def _absent(_node):
    return False

def _node_types(*names):
    found = tuple(t for t in (getattr(exp, n, None) for n in names) if t is not None)
    return found if found else (_absent,)

CONSTRUCTS = {
    "select": {
        "level": 1, "tiers": {1, 2, 3}, "probes": (exp.Select,),
        "hint": "name the columns you want the realm to surrender",
    },
    "where": {
        "level": 2, "tiers": {1, 2, 3}, "probes": (exp.Where,),
        "hint": "narrow the blast radius so it strikes only what you name",
    },
    "order_by": {
        "level": 3, "tiers": {1, 2, 3}, "probes": (exp.Order,),
        "hint": "sequence the data before you commit to strike order",
    },
    "limit": {
        "level": 4, "tiers": {1, 2, 3}, "probes": (exp.Limit,),
        "hint": "cap the yield so the channel does not overload",
    },
    "join": {
        "level": 5, "tiers": {1, 2, 3}, "probes": (exp.Join,),
        "hint": "fuse two data sources into a single incantation",
    },
    "group_by": {
        "level": 6, "tiers": {1, 2, 3}, "probes": (exp.Group,),
        "hint": "collapse many rows into one reading",
    },
    "aggregate": {
        "level": 7, "tiers": {1, 2, 3}, "probes": (exp.AggFunc,),
        "hint": "reduce a column to a single truth: count, sum, average",
    },
    "insert": {
        "level": 8, "tiers": {1, 2, 3}, "probes": (exp.Insert,),
        "hint": "summon a new record into existence",
    },
    "update": {
        "level": 9, "tiers": {1, 2, 3}, "probes": (exp.Update,),
        "hint": "rewrite what already exists",
    },
    "delete": {
        "level": 10, "tiers": {1, 2, 3}, "probes": (exp.Delete,),
        "hint": "banish a record permanently -- this cannot be undone",
    },
    "returning": {
        "level": 10, "tiers": {2, 3}, "probes": _node_types("Returning"),
        "hint": "release the banished record in the same breath as the strike",
    },
    "subquery": {
        "level": 12, "tiers": {1, 2, 3}, "probes": (exp.Subquery,),
        "hint": "nest one incantation inside another",
    },
    "case": {
        "level": 13, "tiers": {1, 2, 3}, "probes": (exp.Case,),
        "hint": "branch the spell on a condition",
    },
    "union": {
        "level": 14, "tiers": {1, 2, 3}, "probes": (exp.Union,),
        "hint": "stack two result sets into one volley",
    },
    "window": {
        "level": 15, "tiers": {1, 2, 3}, "probes": (exp.Window,),
        "hint": "read across neighbouring rows without collapsing them",
    },
    "ilike": {
        "level": 12, "tiers": {3}, "probes": _node_types("ILike"),
        "hint": "match text without caring about case -- only PROD binds this",
    },
    "distinct_on": {
        "level": 13, "tiers": {3}, "probes": (_distinct_on,),
        "hint": "collapse duplicates down to one per group -- PROD only",
    },
    "jsonb": {
        "level": 14, "tiers": {3}, "probes": (_json_ops,),
        "hint": "reach inside a structured glyph -- PROD only",
    },
    "lateral": {
        "level": 15, "tiers": {2, 3}, "probes": _node_types("Lateral"),
        "hint": "let a nested incantation read the row it rides beside",
    },
}

STATEMENT_KINDS = (exp.Select, exp.Insert, exp.Update, exp.Delete)

# ------------------------------------------------------------- schema
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
    ("interrupted",                "[SPELL BACKFIRED] The incantation drew more than the "
                                   "subnet could carry and collapsed."),
    ("no such table",              "[UNBOUND TABLE] The realm has no table by that name."),
    ("no such column",             "[UNBOUND COLUMN] That column is not inscribed here."),
    ("no such function",           "[UNBOUND VERB] The realm does not know that function."),
    ("syntax error",               "[SYNTAX ERROR] The glyphs do not form a valid pattern."),
    ("ambiguous column name",      "[AMBIGUOUS BINDING] Two sources claim that column. "
                                   "Qualify it."),
    ("misuse of aggregate",        "[MISUSED CHANNEL] Aggregates belong in the yield, "
                                   "not the filter."),
    ("UNIQUE constraint failed",   "[COLLISION] A record already holds that key."),
    ("CHECK constraint failed",    "[SEALED] The record violates its own binding."),
]


def detect_constructs(tree):
    present = set()
    for node in tree.walk():
        for name, spec in CONSTRUCTS.items():
            if name in present:
                continue
            for probe in spec["probes"]:
                if callable(probe) and not isinstance(probe, type):
                    hit = probe(node)
                else:
                    hit = isinstance(node, probe)
                if hit:
                    present.add(name)
                    break
    return present


# ------------------------------------------------------ engine adapters
class EngineAdapter:
    """Each realm provides its own connection, DDL, sandbox hooks, and
    error translation. QueryGameEngine only ever talks to this interface."""
    name    = "?"
    dialect = "sqlite"     # sqlglot dialect
    tier    = 0
    file_path = ""

    def connect(self):
        raise NotImplementedError

    def init_schema(self, conn):
        raise NotImplementedError

    def post_connect(self, conn):
        pass

    def begin_query(self):
        pass

    @property
    def last_denial(self):
        return None

    def friendly_error(self, exc):
        return f"[GLYPH REJECTED] {type(exc).__name__}: {exc}"


class DevAdapter(EngineAdapter):
    name, dialect, tier = "DEV", "sqlite", 1

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
    SCHEMA_TABLES = frozenset({"sqlite_master", "sqlite_schema", "sqlite_temp_master"})
    STEP_BUDGET       = 400
    PROGRESS_INTERVAL = 1000

    _ACTION_NAMES = {}
    for _attr in dir(sqlite3):
        if _attr.startswith("SQLITE_"):
            _val = getattr(sqlite3, _attr, None)
            if isinstance(_val, int) and _attr not in ("SQLITE_OK", "SQLITE_DENY",
                                                       "SQLITE_IGNORE"):
                _ACTION_NAMES.setdefault(_val, _attr[7:])

    def __init__(self, file_path="query_world.db"):
        self.file_path = file_path
        self.conn = None
        self._steps = 0
        self._denied_action = None

    def connect(self):
        if os.path.exists(self.file_path):
            try:
                os.remove(self.file_path)
            except OSError as exc:
                print(f"[WARNING] Could not clear world DB: {exc}")
        self.conn = sqlite3.connect(self.file_path)
        self._install_sandbox()
        return self.conn

    def init_schema(self, conn):
        conn.executescript(SQLITE_DDL)
        conn.commit()

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


class StagingAdapter(EngineAdapter):
    """MariaDB. Not yet provisioned -- raises on connect with a clear message."""
    name, dialect, tier = "STAGING", "mariadb", 2
    def __init__(self, dsn="mariadb://localhost/query_world"):
        self.file_path = dsn
    def connect(self):
        raise NotImplementedError(
            "STAGING realm is not provisioned. MariaDB requires a network "
            "server; see MIGRATE for transport status."
        )


class ProdAdapter(EngineAdapter):
    """PostgreSQL. Not yet provisioned."""
    name, dialect, tier = "PROD", "postgres", 3
    def __init__(self, dsn="postgresql://localhost/query_world"):
        self.file_path = dsn
    def connect(self):
        raise NotImplementedError(
            "PROD realm is not provisioned. PostgreSQL requires a network "
            "server; see MIGRATE for transport status."
        )


REALM_ADAPTERS = {1: DevAdapter, 2: StagingAdapter, 3: ProdAdapter}


# -------------------------------------------------------------- engine
class QueryGameEngine:
    MAX_ROWS = 200

    def __init__(self, adapter=None):
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption("QUERY: The Query That Saved The World [v0.3]")
        self.clock = pygame.time.Clock()
        pygame.key.set_repeat(320, 35)

        self.font_large = pygame.font.SysFont("Courier New", 20, bold=True)
        self.font_small = pygame.font.SysFont("Courier New", 14)

        self.adapter = adapter or DevAdapter("query_world.db")
        self.conn = self.adapter.connect()
        self.cursor = self.conn.cursor()
        self.adapter.init_schema(self.conn)
        self.adapter.post_connect(self.conn)

        self.player = {}
        self.mobs = []
        self.hotbar = {}
        self.pending_bind = None
        self.current_input = ""
        self.console = []
        self._migrations_announced = set()

        self.refresh_state()
        self.refresh_hotbar()

        realm = f"{self.adapter.name} / {self.adapter.dialect}"
        self.log(f"[SYSTEM] QUERY OS v1.0.5 :: realm {realm}", COLOR_MUTED)
        self.log("[SYSTEM] Arrow keys translate you through the grid.", COLOR_MUTED)
        self.log("[SYSTEM] Type a query to cast. SELECT is where you begin.",
                 COLOR_ACCENT)

        self.load_assets()

    # -------------------------------------------------------- state access
    def refresh_state(self):
        self.cursor.execute(
            "SELECT level, hp, max_hp, mana, max_mana, position_x, position_y, "
            "zone_tier FROM player_state WHERE id = 1"
        )
        keys = ("level", "hp", "max_hp", "mana", "max_mana",
                "position_x", "position_y", "zone_tier")
        self.player = dict(zip(keys, self.cursor.fetchone()))

        self.cursor.execute(
            "SELECT position_x, position_y, hp FROM hostile_mobs WHERE hp > 0"
        )
        self.mobs = self.cursor.fetchall()

        self._check_migration_stub()

    def refresh_hotbar(self):
        self.cursor.execute(
            "SELECT hotbar_slot, name FROM spellbook WHERE hotbar_slot IS NOT NULL"
        )
        self.hotbar = dict(self.cursor.fetchall())

    def _living_mob_count(self):
        self.cursor.execute("SELECT COUNT(*) FROM hostile_mobs WHERE hp > 0")
        return self.cursor.fetchone()[0]

    def _set_player(self, **fields):
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.cursor.execute(
            f"UPDATE player_state SET {assignments} WHERE id = 1",
            tuple(fields.values()),
        )
        self.conn.commit()

    def log(self, text, color=None):
        self.console.append((text, color if color is not None else COLOR_TEXT))
        if len(self.console) > 80:
            self.console.pop(0)

    # --------------------------------------------------- migration stub
    def _next_realm(self):
        tier = self.adapter.tier
        return tier + 1 if tier < 3 else None

    def _check_migration_stub(self):
        nxt = self._next_realm()
        if nxt is None or nxt in self._migrations_announced:
            return
        if self.player.get("level", 1) < MIGRATION_UNLOCK_LEVEL:
            return
        self._migrations_announced.add(nxt)
        target = REALM_ADAPTERS[nxt]
        self.log(
            f"[SYSTEM] PROVISIONAL MIGRATION AVAILABLE -- realm "
            f"{target.name} ({target.dialect}).", COLOR_ACCENT
        )
        self.log("[SYSTEM] The engine bridge is not yet provisioned. "
                 "Type MIGRATE for status.", COLOR_MUTED)

    def _report_migration_status(self):
        nxt = self._next_realm()
        if nxt is None:
            self.log("[MIGRATION] You stand in the final realm.", COLOR_MUTED)
            return
        target = REALM_ADAPTERS[nxt]
        self.log(f"[MIGRATION] Current  : {self.adapter.name} ({self.adapter.dialect})",
                 COLOR_MUTED)
        self.log(f"[MIGRATION] Target   : {target.name} ({target.dialect})",
                 COLOR_MUTED)
        self.log("[MIGRATION] Status   : PROVISIONED BUT NOT BOUND.", COLOR_WARN)
        self.log("[MIGRATION] Transport: none. Spellbook is safe and remains "
                 "in source dialect.", COLOR_MUTED)
        if self.adapter.tier == 1:
            self.log("[MIGRATION] Upon binding, expect type strictness and "
                     "dialect drift in your engraved spells.", COLOR_MUTED)

    # --------------------------------------------------- mana estimation
    def estimate_mana(self, tree):
        return max(1, int(sum(1 for _ in tree.walk()) * 0.75))

    # ------------------------------------------------------- spell casting
    def execute_spell(self, raw_query, from_hotbar=False):
        query = raw_query.strip().rstrip(";").strip()
        if not query:
            return

        if query.upper() == "MIGRATE":
            self._report_migration_status()
            return

        self.log(f"QUERY> {query}")

        try:
            parsed = [s for s in sqlglot.parse(query, dialect=self.adapter.dialect)
                      if s is not None]
        except ParseError as exc:
            self.log(f"  [PARSE FAILURE] The incantation dissolves: {exc}", COLOR_WARN)
            return

        if not parsed:
            return
        if len(parsed) > 1:
            self.log("  [OVERLOAD] One incantation at a time. The second glyph "
                     "fizzles.", COLOR_WARN)
            return

        tree = parsed[0]
        if not isinstance(tree, STATEMENT_KINDS):
            self.log("  [FORBIDDEN GLYPH] That is not a spell this deck can "
                     "channel.", COLOR_WARN)
            return

        # -- gate 1: pedagogical (level) --------------------------------
        present = detect_constructs(tree)
        too_high = [c for c in present
                    if CONSTRUCTS[c]["level"] > self.player["level"]]
        if too_high:
            worst = max(too_high, key=lambda c: CONSTRUCTS[c]["level"])
            self.log(
                f"  [INSUFFICIENT CIRCUITRY] The {worst.upper()} pattern will not "
                f"resolve. Requires level {CONSTRUCTS[worst]['level']}.", COLOR_WARN
            )
            self.log(f"    hint: {CONSTRUCTS[worst]['hint']}", COLOR_MUTED)
            return

        # -- gate 2: realm availability ---------------------------------
        unbound = [c for c in present if self.adapter.tier not in CONSTRUCTS[c]["tiers"]]
        if unbound:
            worst = unbound[0]
            realms = "/".join(TIER_NAMES[t] for t in sorted(CONSTRUCTS[worst]["tiers"]))
            self.log(
                f"  [REALM MISMATCH] {worst.upper()} has no binding in "
                f"{self.adapter.name}. Only {realms} supports this glyph.", COLOR_WARN
            )
            self.log(f"    hint: {CONSTRUCTS[worst]['hint']}", COLOR_MUTED)
            return

        # -- gate 3: mana -----------------------------------------------
        cost = self.estimate_mana(tree)
        if cost > self.player["mana"]:
            self.log(f"  [MANA EXHAUSTED] {cost} required, {self.player['mana']} "
                     f"available. Move to recover.", COLOR_WARN)
            return

        # -- execute under sandbox --------------------------------------
        self.adapter.begin_query()
        kills_before = self._living_mob_count()

        try:
            self.cursor.execute(query)
            if isinstance(tree, exp.Select):
                rows = self.cursor.fetchmany(self.MAX_ROWS)
                overflow = self.cursor.fetchone() is not None
                self._render_rows(rows, overflow)
            else:
                self.conn.commit()
                self.log(f"  -> [MUTATION APPLIED] {self.cursor.rowcount} row(s) "
                         f"affected.", COLOR_ACCENT)
        except Exception as exc:
            self.log(f"  {self.adapter.friendly_error(exc)}", COLOR_WARN)
            try:
                self.conn.rollback()
            except Exception:
                pass
            return

        # -- success ----------------------------------------------------
        self._set_player(mana=self.player["mana"] - cost)
        self.refresh_state()

        kills = kills_before - self._living_mob_count()
        if kills > 0:
            self.log(f"  [DE-REFERENCED] {kills} hostile(s) purged from the live "
                     f"set.", COLOR_ACCENT)
            for _ in range(kills):
                self.roll_loot_drop(self.player["zone_tier"])
            self.refresh_state()

        if not from_hotbar:
            self.pending_bind = {"query": query, "mana_cost": cost}
            self.log("  [BIND?] Press 1-9 to engrave this spell into a hotbar slot, "
                     "or Esc to release it.", COLOR_ACCENT)

    def _render_rows(self, rows, overflow):
        if not rows:
            self.log("  -> [EMPTY SET: 0 rows]", COLOR_MUTED)
            return
        cols = [d[0] for d in (self.cursor.description or [])]
        widths = []
        for i, col in enumerate(cols):
            longest = max((len(str(r[i])) for r in rows), default=0)
            widths.append(min(max(len(col), longest), 18))
        if cols:
            self.log("  " + " | ".join(c.ljust(w) for c, w in zip(cols, widths)),
                     COLOR_MUTED)
        for row in rows:
            self.log("  " + " | ".join(str(v)[:18].ljust(w)
                                       for v, w in zip(row, widths)), COLOR_ACCENT)
        if overflow:
            self.log(f"  … truncated at {self.MAX_ROWS} rows", COLOR_WARN)

    # ----------------------------------------------------------- hotbar
    def cast_hotbar_slot(self, slot):
        self.cursor.execute(
            "SELECT name, query FROM spellbook WHERE hotbar_slot = ?", (slot,)
        )
        row = self.cursor.fetchone()
        if not row:
            self.log(f"  [EMPTY SLOT] Hotbar {slot} holds no incantation.",
                     COLOR_MUTED)
            return
        name, query = row
        self.log(f"[HOTBAR {slot}] {name}", COLOR_MUTED)
        self.execute_spell(query, from_hotbar=True)

    def bind_spell(self, slot):
        bind, self.pending_bind = self.pending_bind, None
        if not bind:
            return
        name = self._suggest_name(bind["query"])
        self.cursor.execute(
            "INSERT INTO spellbook (name, query, mana_cost, hotbar_slot) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(hotbar_slot) DO UPDATE SET "
            "name = excluded.name, query = excluded.query, "
            "mana_cost = excluded.mana_cost",
            (name, bind["query"], bind["mana_cost"], slot),
        )
        self.conn.commit()
        self.refresh_hotbar()
        self.log(f"  [ENGRAVED] Slot {slot} <- {name}", COLOR_ACCENT)

    @staticmethod
    def _suggest_name(query):
        flat = " ".join(query.split())
        return flat[:22] + ("…" if len(flat) > 22 else "")

    # --------------------------------------------------------- movement
    def try_move(self, dx, dy):
        nx = self.player["position_x"] + dx
        ny = self.player["position_y"] + dy
        if not (0 <= nx < GRID_COLS and 0 <= ny < GRID_ROWS):
            return
        self.cursor.execute(
            "UPDATE player_state SET position_x = ?, position_y = ? WHERE id = 1",
            (nx, ny),
        )
        self.cursor.execute(
            "UPDATE player_state SET mana = MIN(max_mana, mana + ?) WHERE id = 1",
            (MANA_PER_STEP,),
        )
        self.conn.commit()
        self.refresh_state()

    # ------------------------------------------------------------ loot
    def roll_loot_drop(self, zone_tier=1):
        table = [
            {"item": "Scrap CRT Deck Mk.I",      "rarity": "Common",   "slot": "HUD",   "tier": 1, "weight": 50},
            {"item": "Frayed Copper Interface",  "rarity": "Common",   "slot": "CABLE", "tier": 1, "weight": 35},
            {"item": "Overclocked Heat Sink",    "rarity": "Uncommon", "slot": "MOD",   "tier": 2, "weight": 12},
            {"item": "Shielded Subnet Cable",    "rarity": "Uncommon", "slot": "CABLE", "tier": 2, "weight": 8},
            {"item": "Subquery Processing Core", "rarity": "Rare",     "slot": "HUD",   "tier": 3, "weight": 3},
            {"item": "Black Hat Root Console",   "rarity": "Epic",     "slot": "DECK",  "tier": 3, "weight": 1},
        ]
        pool = [i for i in table if i["tier"] <= zone_tier]
        total = sum(i["weight"] for i in pool)
        if not total:
            return None
        roll, cumulative = random.randint(1, total), 0
        for entry in pool:
            cumulative += entry["weight"]
            if roll <= cumulative:
                self.cursor.execute(
                    "INSERT INTO inventory (item_name, rarity, slot_type) "
                    "VALUES (?, ?, ?)",
                    (entry["item"], entry["rarity"], entry["slot"]),
                )
                self.conn.commit()
                self.log(f"[LOOT] ({entry['rarity']}) {entry['item']}", COLOR_ACCENT)
                return entry
        return None

    # ----------------------------------------------------------- assets
    def load_assets(self):
        self.player_img = None
        self.mob_img = None
        try:
            if os.path.exists("assets/chibi_char.png"):
                img = pygame.image.load("assets/chibi_char.png").convert_alpha()
                self.player_img = pygame.transform.scale(img, (32, 32))
            if os.path.exists("assets/chibi_mob.png"):
                img = pygame.image.load("assets/chibi_mob.png").convert_alpha()
                self.mob_img = pygame.transform.scale(img, (28, 28))
        except pygame.error as exc:
            print(f"[WARNING] Asset load failed: {exc}")

    # ----------------------------------------------------------- events
    NUMBER_KEYS = {
        pygame.K_1: 1, pygame.K_2: 2, pygame.K_3: 3, pygame.K_4: 4, pygame.K_5: 5,
        pygame.K_6: 6, pygame.K_7: 7, pygame.K_8: 8, pygame.K_9: 9,
        pygame.K_KP1: 1, pygame.K_KP2: 2, pygame.K_KP3: 3, pygame.K_KP4: 4,
        pygame.K_KP5: 5, pygame.K_KP6: 6, pygame.K_KP7: 7, pygame.K_KP8: 8,
        pygame.K_KP9: 9,
    }

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type != pygame.KEYDOWN:
                continue

            if self.pending_bind:
                if event.key == pygame.K_ESCAPE:
                    self.pending_bind = None
                    self.log("  [BIND CANCELLED] The glyph disperses.", COLOR_MUTED)
                    continue
                if event.key in self.NUMBER_KEYS:
                    self.bind_spell(self.NUMBER_KEYS[event.key])
                    continue
                self.pending_bind = None

            if event.key == pygame.K_UP:
                self.try_move(0, -1); continue
            if event.key == pygame.K_DOWN:
                self.try_move(0, 1); continue
            if event.key == pygame.K_LEFT:
                self.try_move(-1, 0); continue
            if event.key == pygame.K_RIGHT:
                self.try_move(1, 0); continue

            if event.key in self.NUMBER_KEYS and not self.current_input:
                self.cast_hotbar_slot(self.NUMBER_KEYS[event.key])
                continue

            if event.key == pygame.K_F2:
                self._set_player(level=self.player["level"] + 1)
                self.refresh_state()
                self.log(f"[DEV] Level -> {self.player['level']}", COLOR_WARN)
                continue
            if event.key == pygame.K_F3:
                self._set_player(mana=self.player["max_mana"])
                self.refresh_state()
                self.log("[DEV] Mana restored.", COLOR_WARN)
                continue

            if event.key == pygame.K_RETURN:
                if self.current_input.strip():
                    query, self.current_input = self.current_input, ""
                    self.execute_spell(query)
            elif event.key == pygame.K_BACKSPACE:
                self.current_input = self.current_input[:-1]
            elif event.key == pygame.K_ESCAPE:
                self.current_input = ""
            elif event.unicode.isprintable() and event.unicode:
                self.current_input += event.unicode

        return True

    # ---------------------------------------------------------- drawing
    def draw_world(self):
        for x in range(GRID_COLS):
            for y in range(GRID_ROWS):
                pygame.draw.rect(self.screen, COLOR_GRID, pygame.Rect(
                    GRID_ORIGIN_X + x * GRID_SIZE,
                    GRID_ORIGIN_Y + y * GRID_SIZE,
                    GRID_SIZE - 2, GRID_SIZE - 2), 1)

        for mx, my, _hp in self.mobs:
            px = GRID_ORIGIN_X + mx * GRID_SIZE + 6
            py = GRID_ORIGIN_Y + my * GRID_SIZE + 6
            if self.mob_img:
                self.screen.blit(self.mob_img, (px, py))
            else:
                pygame.draw.rect(self.screen, COLOR_DANGER,
                                 pygame.Rect(px, py, GRID_SIZE - 22, GRID_SIZE - 22))

        px = GRID_ORIGIN_X + self.player["position_x"] * GRID_SIZE + 4
        py = GRID_ORIGIN_Y + self.player["position_y"] * GRID_SIZE + 4
        if self.player_img:
            self.screen.blit(self.player_img, (px, py))
        else:
            pygame.draw.rect(self.screen, COLOR_ACCENT,
                             pygame.Rect(px, py, GRID_SIZE - 12, GRID_SIZE - 12))

    @staticmethod
    def wrap(text, width=62):
        lines = []
        for raw in text.split("\n"):
            line = ""
            for word in raw.split(" "):
                if not line:
                    line = word
                elif len(line) + 1 + len(word) <= width:
                    line += " " + word
                else:
                    lines.append(line)
                    line = word
            lines.append(line)
        return lines

    def draw_hud(self):
        panel = pygame.Rect(700, 20, 560, 680)
        pygame.draw.rect(self.screen, COLOR_PANEL, panel)
        pygame.draw.rect(self.screen, COLOR_MUTED, panel, 2)

        self.screen.blit(self.font_large.render(
            "=== QUERY SPELL WORKBENCH ===", True, COLOR_TEXT), (720, 36))

        p = self.player
        status = (f"LVL {p['level']}  HP {p['hp']}/{p['max_hp']}  "
                  f"MANA {p['mana']}/{p['max_mana']}  "
                  f"POS {p['position_x']},{p['position_y']}  "
                  f"REALM {self.adapter.name}")
        self.screen.blit(self.font_small.render(status, True, COLOR_ACCENT),
                         (720, 62))

        top, bottom, line_h = 86, 556, 17
        max_lines = (bottom - top) // line_h
        rendered = []
        for text, color in self.console:
            for chunk in self.wrap(text):
                rendered.append((chunk, color))
        for i, (chunk, color) in enumerate(rendered[-max_lines:]):
            self.screen.blit(self.font_small.render(chunk, True, color),
                             (720, top + i * line_h))

        for slot in range(1, 10):
            box = pygame.Rect(720 + (slot - 1) * 56, 566, 52, 26)
            occupied = slot in self.hotbar
            pygame.draw.rect(self.screen, COLOR_BG, box)
            pygame.draw.rect(self.screen,
                             COLOR_ACCENT if occupied else COLOR_MUTED, box, 1)
            self.screen.blit(self.font_small.render(
                str(slot), True, COLOR_ACCENT if occupied else COLOR_MUTED),
                (box.x + 6, box.y + 5))

        box = pygame.Rect(720, 618, 520, 38)
        pygame.draw.rect(self.screen, COLOR_BG, box)
        pygame.draw.rect(self.screen, COLOR_TEXT, box, 1)

        prompt, shown = "QUERY> ", self.current_input
        if len(prompt) + len(shown) > 58:
            shown = "…" + self.current_input[-(58 - len(prompt) - 1):]
        self.screen.blit(self.font_small.render(f"{prompt}{shown}_", True,
                                                COLOR_TEXT), (733, 629))

        self.screen.blit(self.font_small.render(
            "F2 dev: level+1   F3 dev: mana   MIGRATE: realm status",
            True, COLOR_MUTED), (720, 666))

    def run(self):
        running = True
        while running:
            running = self.handle_events()
            self.screen.fill(COLOR_BG)
            self.draw_world()
            self.draw_hud()
            pygame.display.flip()
            self.clock.tick(FPS)
        self.conn.close()
        pygame.quit()
        sys.exit()


if __name__ == "__main__":
    QueryGameEngine().run()
