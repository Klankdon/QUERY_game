"""QUERY: The Query That Saved The World -- main game (v0.6)

    python game.py                    # DEV mode (default; SQLite)
    python game.py --realm staging    # STAGING only (MariaDB rules)
    python game.py --realm prod       # PROD only (Postgres rules)
    python game.py --all              # DEV -> STAGING -> PROD progression
    python game.py --fresh            # wipe all save files before starting

In single-realm mode (--realm X) MIGRATE audits your spellbook against
your own realm's rules. In --all mode MIGRATE audits against the next
realm, and MIGRATE CONFIRM crosses.
"""
import argparse
import os
import random
import sys

import pygame
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

import map_gen
from db import (
    CONSTRUCTS, STATEMENT_KINDS, SCHEMA_COLUMNS, GRACEFUL_HINTS,
    TIER_NAMES, MIGRATION_UNLOCK_LEVEL,
    SCREEN_WIDTH, SCREEN_HEIGHT, FPS,
    GRID_SIZE, GRID_ORIGIN_X, GRID_ORIGIN_Y,
    COLOR_BG, COLOR_TEXT, COLOR_MUTED, COLOR_ACCENT,
    COLOR_DANGER, COLOR_WARN, COLOR_PANEL, MANA_PER_STEP,
    detect_constructs, detect_dialect_hazards, check_strictness,
)


MOB_TEMPLATES = [
    {"name": "Rogue_Daemon_v1", "hp": 50, "armor_class": 12,
     "weakness": "LIGHTNING", "tier": 1},
    {"name": "Glitch_Crawler",  "hp": 30, "armor_class":  8,
     "weakness": "FIRE",      "tier": 1},
    {"name": "Subnet_Stalker",  "hp": 75, "armor_class": 15,
     "weakness": "COLD",      "tier": 2},
    {"name": "Null_Wraith",     "hp": 90, "armor_class": 16,
     "weakness": "LIGHTNING", "tier": 3},
]


def WALL_HEIGHT_FACE(_sx, _sy):
    return map_gen.WALL_HEIGHT


class QueryGameEngine:
    MAX_ROWS = 200

    def __init__(self, adapter, mode="single"):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption("QUERY: The Query That Saved The World [v0.6]")
        self.clock = pygame.time.Clock()
        pygame.key.set_repeat(320, 35)

        self.font_large = pygame.font.SysFont("Courier New", 20, bold=True)
        self.font_small = pygame.font.SysFont("Courier New", 14)

        self.mode = mode                         # "single" | "all"
        self.adapter = adapter
        needs_init = (
            not os.path.exists(adapter.file_path)
            or os.path.getsize(adapter.file_path) == 0
        )
        self.conn = adapter.connect(reinitialize=False)
        self.cursor = self.conn.cursor()
        if needs_init:
            adapter.init_schema(self.conn)
        adapter.post_connect(self.conn)

        # world map first -- player state depends on spawn position
        self.world = self._world_init_or_load()
        self.cam_x = 0
        self.cam_y = 0
        self._last_terminal_id = None

        self.player = {}
        self.mobs = []
        self.hotbar = {}
        self.pending_bind = None
        self.current_input = ""
        self.console = []
        self._migrations_announced = set()

        self.refresh_state()
        self.refresh_hotbar()

        modes = "progression" if mode == "all" else "single-realm"
        self.log(f"[SYSTEM] QUERY OS v1.6.0 :: {adapter.name} / {adapter.dialect} "
                 f"({modes})", COLOR_MUTED)
        self.log(f"[SYSTEM] World seed {self.world.seed} :: "
                 f"{self.world.width}x{self.world.height} tiles", COLOR_MUTED)
        self.log(f"[SYSTEM] Strictness: {self._rules_summary(adapter)}", COLOR_MUTED)
        self.log("[SYSTEM] Type a query to cast. SELECT is where you begin.",
                 COLOR_ACCENT)

        self.load_assets()

    # ------------------------------------------- world persistence
    def _world_init_or_load(self):
        self.cursor.execute("SELECT COUNT(*) FROM world_map")
        if self.cursor.fetchone()[0] == 0:
            world = map_gen.generate()
            self._persist_world(world)
            self._spawn_mobs(world)
        else:
            world = self._load_world()
            if not world:
                world = map_gen.generate()
                self._persist_world(world)
                self._spawn_mobs(world)
        return world

    def _persist_world(self, world):
        self.cursor.execute(
            "INSERT INTO world_map (id, seed, width, height, spawn_x, spawn_y) "
            "VALUES (1, ?, ?, ?, ?, ?)",
            (world.seed, world.width, world.height, world.spawn_x, world.spawn_y),
        )
        for r in world.rooms:
            self.cursor.execute(
                "INSERT INTO rooms (id, template, name, x, y, w, h, tier, corrupted, "
                "visited, terminal_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (r.id, r.template, r.name, r.x, r.y, r.w, r.h, r.tier,
                 int(r.corrupted), int(r.visited), r.terminal_id),
            )
        for t in world.terminals:
            self.cursor.execute(
                "INSERT INTO terminals (id, room_id, x, y, kind, prompt, "
                "requires_constructs, solved) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                (t.id, t.room_id, t.x, t.y, t.kind, t.prompt, t.requires_constructs),
            )
        for y in range(world.height):
            for x in range(world.width):
                r = world.room_at(x, y)
                self.cursor.execute(
                    "INSERT INTO map_tiles (x, y, tile_type, room_id) "
                    "VALUES (?, ?, ?, ?)",
                    (x, y, world.tiles[y][x], r.id if r else None),
                )
        # seed the player at spawn
        self.cursor.execute(
            "UPDATE player_state SET position_x = ?, position_y = ?, zone_tier = ? "
            "WHERE id = 1",
            (world.spawn_x, world.spawn_y, 1),
        )
        self.conn.commit()

    def _load_world(self):
        self.cursor.execute(
            "SELECT seed, width, height, spawn_x, spawn_y FROM world_map WHERE id = 1"
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        seed, w, h, spawn_x, spawn_y = row

        tiles = [[map_gen.VOID for _ in range(w)] for _ in range(h)]
        self.cursor.execute("SELECT x, y, tile_type FROM map_tiles")
        for x, y, tt in self.cursor.fetchall():
            tiles[y][x] = tt

        rooms = []
        self.cursor.execute(
            "SELECT id, template, name, x, y, w, h, tier, corrupted, visited, terminal_id "
            "FROM rooms ORDER BY id"
        )
        for r in self.cursor.fetchall():
            rooms.append(map_gen.Room(
                id=r[0], template=r[1], name=r[2], x=r[3], y=r[4], w=r[5], h=r[6],
                tier=r[7], corrupted=bool(r[8]), visited=bool(r[9]),
                terminal_id=r[10] if r[10] is not None else -1,
            ))

        terminals = []
        self.cursor.execute(
            "SELECT id, room_id, x, y, kind, prompt, requires_constructs, solved "
            "FROM terminals ORDER BY id"
        )
        for t in self.cursor.fetchall():
            terminals.append(map_gen.Terminal(
                id=t[0], room_id=t[1], x=t[2], y=t[3],
                kind=t[4], prompt=t[5], requires_constructs=t[6], solved=bool(t[7]),
            ))

        return map_gen.WorldMap(seed=seed, width=w, height=h,
                                spawn_x=spawn_x, spawn_y=spawn_y,
                                tiles=tiles, rooms=rooms, terminals=terminals)

    def _spawn_mobs(self, world):
        rng = random.Random(world.seed * 7 + 13)
        candidate_rooms = [r for r in world.rooms if r.id != 0]
        rng.shuffle(candidate_rooms)
        n = min(len(candidate_rooms), 4)
        placed = 0
        for room in candidate_rooms[:n]:
            for _attempt in range(20):
                tx = rng.randint(room.x, room.x + room.w - 1)
                ty = rng.randint(room.y, room.y + room.h - 1)
                if world.tile_at(tx, ty) != map_gen.FLOOR:
                    continue
                if world.terminal_at(tx, ty):
                    continue
                if tx == world.spawn_x and ty == world.spawn_y:
                    continue
                template = rng.choice(
                    [m for m in MOB_TEMPLATES if m["tier"] <= room.tier]
                    or [MOB_TEMPLATES[0]]
                )
                self.cursor.execute(
                    "INSERT INTO hostile_mobs (name, hp, armor_class, "
                    "elemental_weakness, position_x, position_y) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (template["name"], template["hp"], template["armor_class"],
                     template["weakness"], tx, ty),
                )
                placed += 1
                break
        self.conn.commit()
        print(f"[MAP] seed {world.seed} :: {len(world.rooms)} rooms, "
              f"{len(world.terminals)} terminals, {placed} mobs placed")

    # ------------------------------------------- realm helpers
    @property
    def current_dialect(self):
        return self.adapter.dialect

    @property
    def current_rules(self):
        return self.adapter.rules

    @staticmethod
    def _rules_summary(adapter):
        r = adapter.rules
        active = []
        if r.only_full_group_by: active.append("ONLY_FULL_GROUP_BY")
        if r.strict_types:       active.append("STRICT_TYPES")
        return ", ".join(active) if active else "none (permissive by design)"

    def _next_realm(self):
        if self.mode != "all":
            return None
        return self.adapter.tier + 1 if self.adapter.tier < 3 else None

    @staticmethod
    def _make_adapter(tier):
        if tier == 1:
            from sqlite_db import SQLiteAdapter
            return SQLiteAdapter("query_sqlite.db")
        if tier == 2:
            from maria_db import MariaDBAdapter
            return MariaDBAdapter("query_maria.db")
        from postgres_db import PostgresAdapter
        return PostgresAdapter("query_postgres.db")

    # ------------------------------------------ state access
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

        # track current room (for tier + narration)
        room = self.world.room_at(self.player["position_x"], self.player["position_y"])
        if room and not room.visited:
            room.visited = True
            self.cursor.execute("UPDATE rooms SET visited = 1 WHERE id = ?", (room.id,))
            self.conn.commit()
            self.log(f"[ENTER] {room.name} (tier {room.tier}"
                     f"{', corrupted' if room.corrupted else ''})", COLOR_ACCENT)

        self._check_terminal_proximity()
        self._check_migration_stub()

    def _check_terminal_proximity(self):
        px, py = self.player["position_x"], self.player["position_y"]
        nearest = None
        for t in self.world.terminals:
            if t.solved:
                continue
            d = abs(t.x - px) + abs(t.y - py)
            if d <= 1:
                nearest = t
                break
        if nearest and nearest.id != self._last_terminal_id:
            self._last_terminal_id = nearest.id
            self.log(f"[TERMINAL] {nearest.kind.upper()}", COLOR_ACCENT)
            self.log(f"  {nearest.prompt}", COLOR_TEXT)
            if nearest.requires_constructs:
                self.log(f"  requires: {nearest.requires_constructs}", COLOR_MUTED)
        elif nearest is None:
            self._last_terminal_id = None

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

    # ------------------------------------------- migration
    def _check_migration_stub(self):
        nxt = self._next_realm()
        if nxt is None or nxt in self._migrations_announced:
            return
        if self.player.get("level", 1) < MIGRATION_UNLOCK_LEVEL:
            return
        self._migrations_announced.add(nxt)
        target = self._make_adapter(nxt)
        self.log(f"[SYSTEM] PROVISIONAL MIGRATION AVAILABLE -- realm "
                 f"{target.name} ({target.dialect}).", COLOR_ACCENT)
        self.log("[SYSTEM] Type MIGRATE to audit your spellbook.", COLOR_MUTED)

    def _audit(self, target_adapter):
        self.log(f"[MIGRATE] Auditing against {target_adapter.name} "
                 f"({target_adapter.dialect})...", COLOR_ACCENT)
        self.log(f"[MIGRATE] Target rules: {self._rules_summary(target_adapter)}",
                 COLOR_MUTED)

        self.cursor.execute(
            "SELECT name, query, dialect_written FROM spellbook"
        )
        spells = self.cursor.fetchall()
        if not spells:
            self.log("  Spellbook empty. Nothing to audit.", COLOR_MUTED)
            return 0

        total_breaks = 0
        for name, query, dialect_written in spells:
            src_dialect = dialect_written or self.current_dialect
            self.log(f"  - {name}", COLOR_TEXT)
            diag = self._diagnose(query, src_dialect, target_adapter)
            if diag["breaks"]:
                total_breaks += len(diag["breaks"])
                for b in diag["breaks"]:
                    self.log(f"     [BREAKS] {b}", COLOR_DANGER)
            for w in diag["warnings"]:
                self.log(f"     [WARN]   {w}", COLOR_WARN)
            if diag["transpiled"] and diag["transpiled"].strip() != query.strip():
                self.log(f"     [TRANSPILE] {diag['transpiled']}", COLOR_ACCENT)
            if not diag["breaks"] and not diag["warnings"]:
                self.log("     [OK]", COLOR_ACCENT)
        return total_breaks

    def _diagnose(self, query, src_dialect, target_adapter):
        result = {"breaks": [], "warnings": [], "transpiled": None}
        try:
            tree = sqlglot.parse_one(query, dialect=src_dialect)
        except Exception as exc:
            result["breaks"].append(f"cannot re-parse: {exc}")
            return result

        tgt_dialect = target_adapter.dialect
        for c in detect_constructs(tree):
            if target_adapter.tier not in CONSTRUCTS[c]["tiers"]:
                result["breaks"].append(
                    f"{c.upper()} has no binding in the target realm.")

        for rule_id, msg in check_strictness(tree, target_adapter.rules,
                                              SCHEMA_COLUMNS):
            result["breaks"].append(f"[{rule_id}] {msg}")
            hint = GRACEFUL_HINTS.get(rule_id)
            if hint:
                result["warnings"].append(f"hint: {hint}")

        for rule_id, msg in detect_dialect_hazards(query, src_dialect, tgt_dialect):
            result["warnings"].append(f"[{rule_id}] {msg}")

        try:
            result["transpiled"] = tree.sql(dialect=tgt_dialect)
        except Exception:
            pass
        return result

    def _report_migration_status(self):
        if self.mode != "all":
            self.log(f"[MIGRATE] Single-realm mode: {self.adapter.name}.", COLOR_MUTED)
            self.log("[MIGRATE] Auditing spellbook against own rules...",
                     COLOR_ACCENT)
            self._audit(self.adapter)
            return
        nxt = self._next_realm()
        if nxt is None:
            self.log("[MIGRATE] You stand in the final realm.", COLOR_MUTED)
            self._audit(self.adapter)
            return
        target = self._make_adapter(nxt)
        self.log(f"[MIGRATE] Current: {self.adapter.name} "
                 f"({self.adapter.dialect})", COLOR_MUTED)
        self.log(f"[MIGRATE] Target : {target.name} ({target.dialect})",
                 COLOR_MUTED)
        breaks = self._audit(target)
        if breaks == 0:
            self.log("[MIGRATE] No breaks found.", COLOR_ACCENT)
            self.log("[MIGRATE] Type MIGRATE CONFIRM to cross.", COLOR_ACCENT)
        else:
            self.log(f"[MIGRATE] {breaks} break(s). Fix or re-bind before crossing.",
                     COLOR_WARN)

    def _apply_migration(self):
        if self.mode != "all":
            self.log("[MIGRATE] Single-realm mode: crossings disabled.", COLOR_MUTED)
            self.log("[MIGRATE] Launch without --realm to enable progression.",
                     COLOR_MUTED)
            return
        nxt = self._next_realm()
        if nxt is None:
            self.log("[MIGRATE] Final realm reached. Nothing to cross into.",
                     COLOR_MUTED)
            return
        target = self._make_adapter(nxt)
        self._cross_realm(target)
        self.log(f"[MIGRATE] Crossed into {target.name}.", COLOR_ACCENT)
        self.log(f"[MIGRATE] Strictness now enforced: {self._rules_summary(target)}",
                 COLOR_MUTED)
        self.log("[MIGRATE] Spellbook text preserved. Fixes will re-bind in "
                 "the new realm.", COLOR_MUTED)

    def _cross_realm(self, target_adapter):
        self.cursor.execute(
            "SELECT id, handle, level, hp, max_hp, mana, max_mana, "
            "position_x, position_y, zone_tier FROM player_state"
        )
        player_rows = self.cursor.fetchall()
        self.cursor.execute(
            "SELECT name, query, mana_cost, hotbar_slot, times_cast, "
            "dialect_written, created_at FROM spellbook"
        )
        spells = self.cursor.fetchall()

        self.conn.close()

        self.adapter = target_adapter
        self.conn = self.adapter.connect(reinitialize=True)
        self.cursor = self.conn.cursor()
        self.adapter.init_schema(self.conn)
        self.cursor.execute("DELETE FROM player_state")
        self.cursor.execute("DELETE FROM spellbook")
        for row in player_rows:
            self.cursor.execute(
                "INSERT INTO player_state (id, handle, level, hp, max_hp, "
                "mana, max_mana, position_x, position_y, zone_tier) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row
            )
        for s in spells:
            self.cursor.execute(
                "INSERT INTO spellbook (name, query, mana_cost, hotbar_slot, "
                "times_cast, dialect_written, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", s
            )
        self.conn.commit()
        self.world = self._world_init_or_load()
        self.adapter.post_connect(self.conn)     # sandbox ON
        self._migrations_announced = set()
        self.refresh_state()
        self.refresh_hotbar()

    # ------------------------------------------------- mana
    def estimate_mana(self, tree):
        return max(1, int(sum(1 for _ in tree.walk()) * 0.75))

    # --------------------------------------------- spell casting
    def execute_spell(self, raw_query, from_hotbar=False):
        query = raw_query.strip().rstrip(";").strip()
        if not query:
            return

        upper = query.upper()
        if upper == "MIGRATE":
            self._report_migration_status(); return
        if upper == "MIGRATE CONFIRM":
            self._apply_migration(); return

        self.log(f"QUERY> {query}")

        try:
            parsed = [s for s in sqlglot.parse(query, dialect=self.current_dialect)
                      if s is not None]
        except ParseError as exc:
            self.log(f"  [PARSE FAILURE] The incantation dissolves: {exc}",
                     COLOR_WARN); return
        if not parsed:
            return
        if len(parsed) > 1:
            self.log("  [OVERLOAD] One incantation at a time.", COLOR_WARN); return

        tree = parsed[0]
        if not isinstance(tree, STATEMENT_KINDS):
            self.log("  [FORBIDDEN GLYPH] That is not a spell this deck can "
                     "channel.", COLOR_WARN); return

        if self.current_dialect == "mariadb" and "||" in query:
            self.log("  [DIALECT NOTE] '||' is logical OR here, not concatenation. "
                     "Use CONCAT(a, b) if you meant to join strings.", COLOR_WARN)

        # Gate 1: pedagogy
        present = detect_constructs(tree)
        too_high = [c for c in present
                    if CONSTRUCTS[c]["level"] > self.player["level"]]
        if too_high:
            worst = max(too_high, key=lambda c: CONSTRUCTS[c]["level"])
            self.log(f"  [INSUFFICIENT CIRCUITRY] The {worst.upper()} pattern "
                     f"will not resolve. Requires level "
                     f"{CONSTRUCTS[worst]['level']}.", COLOR_WARN)
            self.log(f"    hint: {CONSTRUCTS[worst]['hint']}", COLOR_MUTED)
            return

        # Gate 2: realm tier
        unbound = [c for c in present
                   if self.adapter.tier not in CONSTRUCTS[c]["tiers"]]
        if unbound:
            worst = unbound[0]
            realms = "/".join(TIER_NAMES[t] for t in sorted(CONSTRUCTS[worst]["tiers"]))
            self.log(f"  [REALM MISMATCH] {worst.upper()} has no binding in "
                     f"{self.adapter.name}. Only {realms} supports this glyph.",
                     COLOR_WARN)
            self.log(f"    hint: {CONSTRUCTS[worst]['hint']}", COLOR_MUTED)
            return

        # Gate 3: strictness
        violations = check_strictness(tree, self.current_rules, SCHEMA_COLUMNS)
        if violations:
            rule_id, msg = violations[0]
            self.log(f"  [REALM REJECTED] {msg}", COLOR_WARN)
            hint = GRACEFUL_HINTS.get(rule_id)
            if hint:
                self.log(f"    hint: {hint}", COLOR_MUTED)
            return

        # Gate 4: mana
        cost = self.estimate_mana(tree)
        if cost > self.player["mana"]:
            self.log(f"  [MANA EXHAUSTED] {cost} required, "
                     f"{self.player['mana']} available. Move to recover.",
                     COLOR_WARN); return

        # Execute under sandbox
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
            try: self.conn.rollback()
            except Exception: pass
            return

        self._set_player(mana=self.player["mana"] - cost)
        self.refresh_state()

        kills = kills_before - self._living_mob_count()
        if kills > 0:
            self.log(f"  [DE-REFERENCED] {kills} hostile(s) purged.", COLOR_ACCENT)
            for _ in range(kills):
                self.roll_loot_drop(self.player["zone_tier"])
            self.refresh_state()

        if not from_hotbar:
            self.pending_bind = {"query": query, "mana_cost": cost}
            self.log("  [BIND?] Press 1-9 to engrave, or Esc to release.",
                     COLOR_ACCENT)

    def _render_rows(self, rows, overflow):
        if not rows:
            self.log("  -> [EMPTY SET: 0 rows]", COLOR_MUTED); return
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
            self.log(f"  ... truncated at {self.MAX_ROWS} rows", COLOR_WARN)

    # ------------------------------------------- hotbar
    def cast_hotbar_slot(self, slot):
        self.cursor.execute(
            "SELECT name, query FROM spellbook WHERE hotbar_slot = ?", (slot,))
        row = self.cursor.fetchone()
        if not row:
            self.log(f"  [EMPTY SLOT] Hotbar {slot} holds no incantation.",
                     COLOR_MUTED); return
        name, query = row
        self.log(f"[HOTBAR {slot}] {name}", COLOR_MUTED)
        self.cursor.execute(
            "UPDATE spellbook SET times_cast = times_cast + 1 "
            "WHERE hotbar_slot = ?", (slot,))
        self.conn.commit()
        self.execute_spell(query, from_hotbar=True)

    def bind_spell(self, slot):
        bind, self.pending_bind = self.pending_bind, None
        if not bind:
            return
        name = self._suggest_name(bind["query"])
        self.cursor.execute(
            "INSERT INTO spellbook (name, query, mana_cost, hotbar_slot, "
            "dialect_written) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(hotbar_slot) DO UPDATE SET "
            "name = excluded.name, query = excluded.query, "
            "mana_cost = excluded.mana_cost, "
            "dialect_written = excluded.dialect_written",
            (name, bind["query"], bind["mana_cost"], slot, self.current_dialect),
        )
        self.conn.commit()
        self.refresh_hotbar()
        self.log(f"  [ENGRAVED] Slot {slot} <- {name}", COLOR_ACCENT)
        self.log(f"    dialect_written = {self.current_dialect}", COLOR_MUTED)

    @staticmethod
    def _suggest_name(query):
        flat = " ".join(query.split())
        return flat[:22] + ("..." if len(flat) > 22 else "")

    # ------------------------------------------- movement
    def try_move(self, dx, dy):
        nx = self.player["position_x"] + dx
        ny = self.player["position_y"] + dy
        tile = self.world.tile_at(nx, ny)
        if tile != map_gen.FLOOR:
            if tile == map_gen.CORRUPTED:
                self.log("[VOID] The Nothingness is unyielding here. Mend the "
                         "schema to restore passage.", COLOR_WARN)
            return
        room = self.world.room_at(nx, ny)
        zone = room.tier if room else self.player["zone_tier"]
        self.cursor.execute(
            "UPDATE player_state SET position_x = ?, position_y = ?, "
            "mana = MIN(max_mana, mana + ?), zone_tier = ? WHERE id = 1",
            (nx, ny, MANA_PER_STEP, zone),
        )
        self.conn.commit()
        if room and zone != self.player["zone_tier"]:
            self.log(f"[ZONE] Entered tier {zone} sector.", COLOR_ACCENT)
        self.refresh_state()

    # ------------------------------------------- loot
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

    # ------------------------------------------- assets
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

    # ------------------------------------------- events
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
                    self.bind_spell(self.NUMBER_KEYS[event.key]); continue
                self.pending_bind = None

            if event.key == pygame.K_UP:    self.try_move(0, -1);  continue
            if event.key == pygame.K_DOWN:  self.try_move(0, 1);   continue
            if event.key == pygame.K_LEFT:  self.try_move(-1, 0);  continue
            if event.key == pygame.K_RIGHT: self.try_move(1, 0);   continue

            if event.key in self.NUMBER_KEYS and not self.current_input:
                self.cast_hotbar_slot(self.NUMBER_KEYS[event.key]); continue

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

    # ------------------------------------------- camera & drawing
    def _compute_camera(self):
        TILE = GRID_SIZE
        view_w, view_h = 700, 720        # left panel is 700px wide
        px = self.player["position_x"] * TILE + TILE // 2
        py = self.player["position_y"] * TILE + TILE // 2
        self.cam_x = max(0, min(px - view_w // 2,
                                self.world.width * TILE - view_w))
        self.cam_y = max(0, min(py - view_h // 2,
                                self.world.height * TILE - view_h))

    def _tile_to_screen(self, tx, ty):
        return (tx * GRID_SIZE - self.cam_x + GRID_ORIGIN_X,
                ty * GRID_SIZE - self.cam_y + GRID_ORIGIN_Y)

    def _visible_tile_range(self):
        TILE = GRID_SIZE
        x0 = max(0, self.cam_x // TILE - 1)
        y0 = max(0, self.cam_y // TILE - 1)
        x1 = min(self.world.width, x0 + (700 // TILE) + 3)
        y1 = min(self.world.height, y0 + (720 // TILE) + 3)
        return x0, y0, x1, y1

    def draw_world(self):
        self._compute_camera()
        TILE = GRID_SIZE
        x0, y0, x1, y1 = self._visible_tile_range()

        # 1. Floors + corrupted (drawn on the ground plane)
        for ty in range(y0, y1):
            for tx in range(x0, x1):
                tile = self.world.tile_at(tx, ty)
                sx, sy = self._tile_to_screen(tx, ty)
                if tile == map_gen.FLOOR:
                    pygame.draw.rect(self.screen, (22, 26, 34),
                                     (sx, sy, TILE, TILE))
                    pygame.draw.rect(self.screen, (30, 36, 48),
                                     (sx, sy, TILE, TILE), 1)
                elif tile == map_gen.CORRUPTED:
                    pulse = int(14 + 8 * abs((pygame.time.get_ticks() % 900) - 450) / 450)
                    pygame.draw.rect(self.screen, (pulse, 0, pulse // 2 + 4),
                                     (sx, sy, TILE, TILE))
                    pygame.draw.rect(self.screen, (60, 0, 40),
                                     (sx, sy, TILE, TILE), 1)

        # 2. Build Y-sort queue: walls + terminals + mobs + player
        queue = []

        for ty in range(y0, y1):
            for tx in range(x0, x1):
                if self.world.tile_at(tx, ty) == map_gen.WALL:
                    queue.append(((ty + 1) * TILE, "wall", tx, ty))

        for t in self.world.terminals:
            if x0 <= t.x < x1 and y0 <= t.y < y1:
                queue.append(((t.y + 1) * TILE, "terminal", t.x, t.y, t))

        for mx, my, _hp in self.mobs:
            if x0 <= mx < x1 and y0 <= my < y1:
                queue.append(((my + 1) * TILE, "mob", mx, my))

        queue.append(((self.player["position_y"] + 1) * TILE, "player", None, None))

        queue.sort(key=lambda e: e[0])

        # 3. Draw in Y order
        for entry in queue:
            kind = entry[1]
            if kind == "wall":
                _y, _k, tx, ty = entry
                sx, sy = self._tile_to_screen(tx, ty)
                wh = WALL_HEIGHT_FACE(sx, sy)
                pygame.draw.rect(self.screen, (40, 46, 58),
                                 (sx, sy - wh, TILE, wh + TILE))
                pygame.draw.rect(self.screen, (68, 78, 96),
                                 (sx, sy - wh, TILE, wh))
                pygame.draw.line(self.screen, (110, 130, 160),
                                 (sx, sy - wh), (sx + TILE, sy - wh), 1)
            elif kind == "terminal":
                _y, _k, tx, ty, _t = entry
                sx, sy = self._tile_to_screen(tx, ty)
                pulse = int(180 + 60 * abs((pygame.time.get_ticks() % 700) - 350) / 350)
                pygame.draw.rect(self.screen, (0, pulse, min(255, pulse + 40)),
                                 (sx + 10, sy + TILE - 30, TILE - 20, 24), 0)
                pygame.draw.rect(self.screen, (0, 255, 200),
                                 (sx + 10, sy + TILE - 30, TILE - 20, 24), 1)
            elif kind == "mob":
                _y, _k, mx, my = entry
                sx, sy = self._tile_to_screen(mx, my)
                if self.mob_img:
                    self.screen.blit(self.mob_img,
                                     (sx + 6, sy + TILE - self.mob_img.get_height() - 4))
                else:
                    pygame.draw.rect(self.screen, COLOR_DANGER,
                                     (sx + 8, sy + TILE - 28, TILE - 16, TILE - 16))
            elif kind == "player":
                px, py = self.player["position_x"], self.player["position_y"]
                sx, sy = self._tile_to_screen(px, py)
                if self.player_img:
                    self.screen.blit(self.player_img,
                                     (sx + 4, sy + TILE - self.player_img.get_height() - 4))
                else:
                    pygame.draw.rect(self.screen, COLOR_ACCENT,
                                     (sx + 4, sy + TILE - 32, TILE - 12, TILE - 12))

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
            shown = "..." + self.current_input[-(58 - len(prompt) - 1):]
        self.screen.blit(self.font_small.render(f"{prompt}{shown}_", True,
                                                COLOR_TEXT), (733, 629))

        hint = ("F2 lvl  F3 mana  MIGRATE audit"
                + ("  MIGRATE CONFIRM cross" if self.mode == "all"
                   else "  (single-realm mode)"))
        self.screen.blit(self.font_small.render(hint, True, COLOR_MUTED),
                         (720, 666))

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


# ------------------------------------------------------- CLI
def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="query",
        description="QUERY: The Query That Saved The World (v0.6)",
        epilog=("Run with no arguments for DEV mode (SQLite, permissive). "
                "Use --realm to lock into one engine; use --all to progress "
                "through DEV -> STAGING -> PROD."),
    )
    parser.add_argument("--realm", choices=["dev", "staging", "prod"],
                        help="Lock into one realm only. No migration upward.")
    parser.add_argument("--all", action="store_true",
                        help="Full progression: start DEV, migrate up to PROD.")
    parser.add_argument("--fresh", action="store_true",
                        help="Wipe all save files before starting.")
    args = parser.parse_args(argv)

    if args.all and args.realm:
        parser.error("--all and --realm are mutually exclusive.")

    if args.fresh:
        for f in ("query_sqlite.db", "query_maria.db", "query_postgres.db"):
            if os.path.exists(f):
                try: os.remove(f)
                except OSError as exc:
                    print(f"[WARN] Could not remove {f}: {exc}")

    if args.all:
        start_tier, mode = 1, "all"
    elif args.realm:
        start_tier = {"dev": 1, "staging": 2, "prod": 3}[args.realm]
        mode = "single"
    else:
        start_tier, mode = 1, "single"

    if start_tier == 1:
        from sqlite_db import SQLiteAdapter
        adapter = SQLiteAdapter("query_sqlite.db")
    elif start_tier == 2:
        from maria_db import MariaDBAdapter
        adapter = MariaDBAdapter("query_maria.db")
    else:
        from postgres_db import PostgresAdapter
        adapter = PostgresAdapter("query_postgres.db")

    QueryGameEngine(adapter=adapter, mode=mode).run()


if __name__ == "__main__":
    main()
