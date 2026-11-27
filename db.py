"""QUERY: The Query That Saved The World -- shared foundation (v0.5)

Everything realm-agnostic:

  * CONSTRUCTS, STATEMENT_KINDS    -- the SQL curriculum
  * SCHEMA_COLUMNS                 -- column types for strict-type checking
  * StrictRules, check_strictness  -- pre-execution validators
  * detect_dialect_hazards         -- "same text, different meaning" probes
  * EngineAdapter                  -- base class every realm implements
  * detect_constructs              -- AST walk -> pedagogical hit set

Deliberately imports no database driver. Each adapter module imports its
own client; this file only depends on sqlglot. `import db` is safe on a
machine missing psycopg2 or mysqlclient.
"""
from sqlglot import exp


# ------------------------------------------------------------ display
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


# ----------------------------------------------------------- schema types
# Column types per table. Used by strict-type checks. Every adapter's DDL
# must match this declaration.
SCHEMA_COLUMNS = {
    "hostile_mobs": {
        "id": "INTEGER", "name": "TEXT", "hp": "INTEGER",
        "armor_class": "INTEGER", "elemental_weakness": "TEXT",
        "position_x": "INTEGER", "position_y": "INTEGER",
    },
    "conduits": {
        "id": "INTEGER", "zone": "TEXT", "status": "TEXT", "voltage": "INTEGER",
    },
    "inventory": {
        "id": "INTEGER", "item_name": "TEXT", "rarity": "TEXT", "slot_type": "TEXT",
    },
    "player_state": {
        "id": "INTEGER", "handle": "TEXT", "level": "INTEGER",
        "hp": "INTEGER", "max_hp": "INTEGER", "mana": "INTEGER",
        "max_mana": "INTEGER", "position_x": "INTEGER", "position_y": "INTEGER",
        "zone_tier": "INTEGER",
    },
    "spellbook": {
        "id": "INTEGER", "name": "TEXT", "query": "TEXT",
        "mana_cost": "INTEGER", "hotbar_slot": "INTEGER",
        "times_cast": "INTEGER", "created_at": "TEXT",
        "dialect_written": "TEXT",
    },
}


# ------------------------------------------------ construct probes
def _distinct_on(node):
    return isinstance(node, exp.Distinct) and node.args.get("on") is not None

def _json_ops(node):
    return isinstance(node, (exp.JSONExtract, exp.JSONExtractScalar))

def _absent(_node):
    return False

def _node_types(*names):
    found = tuple(t for t in (getattr(exp, n, None) for n in names) if t is not None)
    return found if found else (_absent,)

def _cte_present(node):
    return isinstance(node, getattr(exp, "CTE", type(None)))

def _transaction_present(node):
    for n in ("Transaction", "Commit", "Rollback"):
        t = getattr(exp, n, None)
        if t is not None and isinstance(node, t):
            return True
    return False

def _view_present(node):
    return isinstance(node, exp.Create) and \
        str(node.args.get("kind", "")).upper() == "VIEW"

def _index_present(node):
    return isinstance(node, exp.Create) and \
        str(node.args.get("kind", "")).upper() == "INDEX"

def _null_semantic_present(node):
    types = tuple(t for t in (getattr(exp, n, None) for n in
                              ("Coalesce", "Nullif")) if t is not None)
    if types and isinstance(node, types):
        return True
    if isinstance(node, exp.Is):
        return isinstance(node.args.get("expression"), exp.Null)
    return False

def _constraint_present(node):
    types = tuple(t for t in (getattr(exp, n, None) for n in
                              ("PrimaryKey", "ForeignKey",
                               "UniqueColumnConstraint")) if t is not None)
    return bool(types) and isinstance(node, types)


CONSTRUCTS = {
    "select":        {"level": 1,  "tiers": {1, 2, 3}, "probes": (exp.Select,),
                      "hint": "name the columns you want the realm to surrender"},
    "where":         {"level": 2,  "tiers": {1, 2, 3}, "probes": (exp.Where,),
                      "hint": "narrow the blast radius so it strikes only what you name"},
    "order_by":      {"level": 3,  "tiers": {1, 2, 3}, "probes": (exp.Order,),
                      "hint": "sequence the data before you commit to strike order"},
    "limit":         {"level": 4,  "tiers": {1, 2, 3}, "probes": (exp.Limit,),
                      "hint": "cap the yield so the channel does not overload"},
    "join":          {"level": 5,  "tiers": {1, 2, 3}, "probes": (exp.Join,),
                      "hint": "fuse two data sources into a single incantation"},
    "group_by":      {"level": 6,  "tiers": {1, 2, 3}, "probes": (exp.Group,),
                      "hint": "collapse many rows into one reading"},
    "aggregate":     {"level": 7,  "tiers": {1, 2, 3}, "probes": (exp.AggFunc,),
                      "hint": "reduce a column to a single truth: count, sum, average"},
    "insert":        {"level": 8,  "tiers": {1, 2, 3}, "probes": (exp.Insert,),
                      "hint": "summon a new record into existence"},
    "update":        {"level": 9,  "tiers": {1, 2, 3}, "probes": (exp.Update,),
                      "hint": "rewrite what already exists"},
    "delete":        {"level": 10, "tiers": {1, 2, 3}, "probes": (exp.Delete,),
                      "hint": "banish a record permanently -- this cannot be undone"},
    "returning":     {"level": 10, "tiers": {2, 3},    "probes": _node_types("Returning"),
                      "hint": "release the banished record in the same breath as the strike"},
    "null_semantics":{"level": 11, "tiers": {1, 2, 3}, "probes": (_null_semantic_present,),
                      "hint": "the void: NULL is not zero, NULL is not empty, NULL is not NULL"},
    "subquery":      {"level": 12, "tiers": {1, 2, 3}, "probes": (exp.Subquery,),
                      "hint": "nest one incantation inside another"},
    "case":          {"level": 13, "tiers": {1, 2, 3}, "probes": (exp.Case,),
                      "hint": "branch the spell on a condition"},
    "union":         {"level": 14, "tiers": {1, 2, 3}, "probes": (exp.Union,),
                      "hint": "stack two result sets into one volley"},
    "window":        {"level": 15, "tiers": {1, 2, 3}, "probes": (exp.Window,),
                      "hint": "read across neighbouring rows without collapsing them"},
    "ilike":         {"level": 12, "tiers": {3},       "probes": _node_types("ILike"),
                      "hint": "match text without caring about case -- only PROD binds this"},
    "distinct_on":   {"level": 13, "tiers": {3},       "probes": (_distinct_on,),
                      "hint": "collapse duplicates down to one per group -- PROD only"},
    "jsonb":         {"level": 14, "tiers": {3},       "probes": (_json_ops,),
                      "hint": "reach inside a structured glyph -- PROD only"},
    "lateral":       {"level": 15, "tiers": {2, 3},    "probes": _node_types("Lateral"),
                      "hint": "let a nested incantation read the row it rides beside"},
    "cte":           {"level": 16, "tiers": {1, 2, 3}, "probes": (_cte_present,),
                      "hint": "pre-cast a spell, name it, then cast from it"},
    "transaction":   {"level": 17, "tiers": {1, 2, 3}, "probes": (_transaction_present,),
                      "hint": "ritual casting: all components succeed, or none do"},
    "view":          {"level": 18, "tiers": {1, 2, 3}, "probes": (_view_present,),
                      "hint": "a persistent illusion -- a table that is not there"},
    "index":         {"level": 19, "tiers": {1, 2, 3}, "probes": (_index_present,),
                      "hint": "haste binding -- the realm finds your target faster"},
    "constraint":    {"level": 20, "tiers": {1, 2, 3}, "probes": (_constraint_present,),
                      "hint": "make a lie impossible before it is told"},
}
STATEMENT_KINDS = (exp.Select, exp.Insert, exp.Update, exp.Delete,
                   exp.Create, exp.Drop, exp.Alter)


# ---------------------------------------------------- strictness rules
class StrictRules:
    """Per-engine rule toggles that a beginner most often trips on.
    DEV represents SQLite's permissiveness (all flags off).
    STAGING/PROD mirror MariaDB 5.7+ / Postgres defaults (both on)."""
    __slots__ = ("only_full_group_by", "strict_types")

    def __init__(self, only_full_group_by=False, strict_types=False):
        self.only_full_group_by = only_full_group_by
        self.strict_types = strict_types


GRACEFUL_HINTS = {
    "ONLY_FULL_GROUP_BY": "Add the column to GROUP BY, or wrap it in MAX()/MIN(). "
                          "SQLite picked an arbitrary row and let you believe it.",
    "TYPE_INTEGER":       "Pass a numeric literal, not a quoted string. "
                          "'0' is not 0 to a strict engine.",
    "PIPE_CONCAT":        "Use CONCAT(a, b) instead of || for portable code. "
                          "MariaDB reads || as logical OR unless PIPES_AS_CONCAT is set.",
}


# -------------------------------------------- strictness validators
def _unwrap(v):
    while isinstance(v, (exp.Paren, exp.Cast)):
        v = v.this
    return v


def _check_group_by(tree):
    out = []
    for select in tree.find_all(exp.Select):
        group = select.args.get("group")
        if not group:
            continue
        grouped_full, grouped_cols, positional = set(), set(), set()
        for g in group.expressions:
            grouped_full.add(str(g))
            if isinstance(g, exp.Column):
                grouped_cols.add(g.name)
            if str(g).strip().isdigit():
                positional.add(int(str(g).strip()))
        for idx, proj in enumerate(select.expressions, start=1):
            if idx in positional:
                continue
            proj_expr = proj.this if isinstance(proj, exp.Alias) else proj
            if any(isinstance(n, exp.Star) for n in proj_expr.walk()):
                out.append(("ONLY_FULL_GROUP_BY",
                            "SELECT * in a GROUP BY query is not portable."))
                continue
            if isinstance(proj_expr, exp.Literal):
                continue
            if any(isinstance(n, exp.AggFunc) for n in proj_expr.walk()):
                continue
            cols = list(proj_expr.find_all(exp.Column))
            if not cols:
                continue
            covered = True
            for c in cols:
                if str(c) in grouped_full or c.name in grouped_cols:
                    continue
                covered = False
                break
            if covered:
                continue
            label = proj.alias_or_name or str(proj_expr)
            out.append(("ONLY_FULL_GROUP_BY",
                        f"'{label}' references a column not covered by GROUP BY "
                        f"and is not inside an aggregate."))
    return out


def _check_types(tree, schema):
    out = []
    for ins in tree.find_all(exp.Insert):
        into = ins.this
        if isinstance(into, exp.Schema):
            table = str(into.this.name) if hasattr(into.this, "name") else str(into.this)
            col_nodes = list(into.expressions)
        else:
            table = str(into.name) if hasattr(into, "name") else str(into)
            col_nodes = []
        tbl = schema.get(table)
        if not tbl:
            continue
        values = ins.expression
        if not isinstance(values, exp.Values):
            continue
        for tup in values.expressions:
            for idx, val in enumerate(tup.expressions):
                if idx >= len(col_nodes):
                    continue
                col_node = col_nodes[idx]
                col_name = col_node.name if hasattr(col_node, "name") else str(col_node)
                if tbl.get(col_name) != "INTEGER":
                    continue
                v = _unwrap(val)
                if isinstance(v, exp.Null):
                    continue
                if isinstance(v, exp.Literal) and v.is_string:
                    try:
                        int(v.this)
                        continue
                    except (ValueError, TypeError):
                        out.append((
                            "TYPE_INTEGER",
                            f"column '{col_name}' on '{table}' is INTEGER, "
                            f"but you wrote the string '{v.this}'."
                        ))
    return out


def check_strictness(tree, rules, schema):
    violations = []
    if rules.only_full_group_by:
        violations.extend(_check_group_by(tree))
    if rules.strict_types:
        violations.extend(_check_types(tree, schema))
    return violations


def detect_dialect_hazards(sql, source_dialect, target_dialect):
    import sqlglot
    hazards = []
    try:
        src = sqlglot.parse_one(sql, dialect=source_dialect)
        tgt = sqlglot.parse_one(sql, dialect=target_dialect)
    except Exception:
        return hazards
    if source_dialect in ("sqlite", "postgres") and target_dialect in ("mariadb", "mysql"):
        if any(isinstance(n, exp.DPipe) for n in src.walk()) and \
           any(isinstance(n, exp.Or) for n in tgt.walk()):
            hazards.append((
                "PIPE_CONCAT",
                "'||' reads as string concatenation here, but MariaDB reads "
                "it as logical OR. Your query silently changes meaning."
            ))
    return hazards


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


# ----------------------------------------------- adapter base class
class EngineAdapter:
    """Each realm provides its own connection, DDL, sandbox hooks,
    strictness rules, and error translation. The game only ever talks to
    this interface -- which is why adding a new realm never touches game.py."""

    name    = "?"
    dialect = "sqlite"
    tier    = 0
    file_path = ""
    rules   = StrictRules()

    def connect(self, reinitialize=False):
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
