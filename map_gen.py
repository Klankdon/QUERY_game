"""map_gen.py -- procedural 2.5D cyberpunk world generator (v0.6)

Two premises, both realized in code:

  1. The world is a set of MALFORMED TABLES. The map is generated and stored
     as actual rows the player can SELECT. Fixing the world means repairing
     the schema. The map IS a table.

  2. The evil is an ABSENCE, not a presence. Corrupted tiles are not themed
     red -- they are pockets of nothing. Pulsing dark voids where the world
     used to be. Terminals are the only nodes of order left in the ruins.

Layout algorithm:
  * Room-template placer -- hand-shaped rooms for cyberpunk character, not
    organic caves.
  * L-shaped corridors connect rooms through the void.
  * Unreachable floor is culled; walls auto-generate around remaining floor.
  * Tier assigned by distance from spawn: slums -> industrial core -> vault.

Rendering contract:
  * Orthographic 2.5D. Floors are flat rects. Walls are drawn as a rect from
    (sx, sy - WALL_HEIGHT) to (sx + TILE, sy + TILE) with a bevel.
  * Walls and entities share a Y-sort queue keyed on tile foot. Occlusion
    reads correctly: things north of a wall draw behind it.
"""
import random
from dataclasses import dataclass

# --------------------------------------------------------------- tiles
VOID      = "void"       # the Nothingness -- impassable, drawn black
FLOOR     = "floor"      # solid ground -- walkable
WALL      = "wall"       # perimeter wall -- impassable, drawn raised
CORRUPTED = "corrupted"  # ground being eaten -- impassable until mended

WALL_HEIGHT = 20  # px the wall "rises" above the tile footprint

# ------------------------------------------------------------- rooms
# (template, min_w, max_w, min_h, max_h, weight)
ROOM_TEMPLATES = [
    ("slum_alley",      4, 7,  3, 5, 3),
    ("frayed_conduit",  3, 5,  3, 5, 2),
    ("server_farm",     6, 9,  4, 6, 2),
    ("data_exchange",   6, 8,  5, 7, 2),
    ("corporate_lobby", 5, 8,  5, 6, 2),
    ("subnet_core",     7, 10, 6, 8, 1),
    ("archive_vault",   6, 9,  6, 8, 1),
    ("broken_plaza",    7, 10, 7, 9, 1),
]

ROOM_PREFIXES = ["sector_7", "block_4", "corp_9", "node_12",
                 "zone_3", "ward_8", "under_2", "ring_6"]

# --------------------------------------------------- terminal content
# Each entry: (prompt, requires_constructs_csv)
TERMINAL_PROMPTS = {
    "repair": [
        ("The Nothingness has eaten rows from hostile_mobs. "
         "Rewrite one with UPDATE.",
         "update"),
        ("Three records claim the same position. GROUP BY to find the collision.",
         "select,group_by,aggregate"),
        ("An inventory row was de-referenced. Call one back with INSERT.",
         "insert"),
        ("A conduit is misaligned. Sort + filter to isolate it.",
         "select,where,order_by"),
        ("Two tables have drifted. JOIN them to see the gap.",
         "select,join"),
        ("A banished record left no trace. Use RETURNING to read as you strike.",
         "delete,returning"),
    ],
    "lore": [
        ("Fragment 0x01: 'Entropy was not spontaneous. It was scheduled.'", ""),
        ("Fragment 0x02: 'The schema was never the point. The repair was.'", ""),
        ("Fragment 0x03: 'Every restored row is a stitch in the map.'", ""),
        ("Fragment 0x04: 'Someone is deleting us, row by row, from the inside.'", ""),
    ],
}

# ----------------------------------------------------------- records
@dataclass
class Room:
    id: int
    template: str
    name: str
    x: int
    y: int
    w: int
    h: int
    tier: int = 1
    corrupted: bool = False
    visited: bool = False
    terminal_id: int = -1


@dataclass
class Terminal:
    id: int
    room_id: int
    x: int
    y: int
    kind: str                 # 'repair' | 'lore'
    prompt: str
    requires_constructs: str
    solved: bool = False


@dataclass
class WorldMap:
    seed: int
    width: int
    height: int
    spawn_x: int
    spawn_y: int
    tiles: list                    # list[list[str]], row-major
    rooms: list                    # list[Room]
    terminals: list                # list[Terminal]

    def tile_at(self, x, y):
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.tiles[y][x]
        return VOID

    def room_at(self, x, y):
        for r in self.rooms:
            if r.x <= x < r.x + r.w and r.y <= y < r.y + r.h:
                return r
        return None

    def terminal_at(self, x, y):
        for t in self.terminals:
            if t.x == x and t.y == y:
                return t
        return None


# ------------------------------------------------------- generation
def _overlaps_any(x, y, w, h, rooms, pad=2):
    for r in rooms:
        if (x < r.x + r.w + pad and x + w + pad > r.x and
                y < r.y + r.h + pad and y + h + pad > r.y):
            return True
    return False


def _weighted_choice(rng, entries):
    total = sum(e[-1] for e in entries)
    roll = rng.uniform(0, total)
    upto = 0
    for e in entries:
        upto += e[-1]
        if roll <= upto:
            return e
    return entries[-1]


def _carve_corridor(tiles, x1, y1, x2, y2):
    x, y = x1, y1
    while x != x2:
        tiles[y][x] = FLOOR if tiles[y][x] == VOID else tiles[y][x]
        x += 1 if x2 > x1 else -1
    while y != y2:
        tiles[y][x] = FLOOR if tiles[y][x] == VOID else tiles[y][x]
        y += 1 if y2 > y1 else -1


def _flood_reachable(tiles, sx, sy, w, h):
    seen = set()
    stack = [(sx, sy)]
    while stack:
        x, y = stack.pop()
        if (x, y) in seen:
            continue
        if not (0 <= x < w and 0 <= y < h):
            continue
        if tiles[y][x] not in (FLOOR, CORRUPTED):
            continue
        seen.add((x, y))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            stack.append((x + dx, y + dy))
    return seen


def _cull_unreachable(tiles, sx, sy, w, h):
    reachable = _flood_reachable(tiles, sx, sy, w, h)
    for y in range(h):
        for x in range(w):
            if tiles[y][x] in (FLOOR, CORRUPTED) and (x, y) not in reachable:
                tiles[y][x] = VOID


def _apply_walls(tiles, w, h):
    walls = []
    for y in range(h):
        for x in range(w):
            if tiles[y][x] == VOID:
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                                (1, 1), (-1, -1), (1, -1), (-1, 1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and tiles[ny][nx] == FLOOR:
                        walls.append((x, y))
                        break
    for (x, y) in walls:
        tiles[y][x] = WALL


def _corrupt_room(tiles, room, rng, probability=0.5):
    """Eat the room's interior edges. Center cross stays walkable so the
    player can always reach the terminal."""
    cx = room.x + room.w // 2
    cy = room.y + room.h // 2
    for x in range(room.x, room.x + room.w):
        for y in range(room.y, room.y + room.h):
            if x == cx or y == cy:
                continue
            if tiles[y][x] != FLOOR:
                continue
            is_edge = (x == room.x or x == room.x + room.w - 1 or
                       y == room.y or y == room.y + room.h - 1)
            if is_edge and rng.random() < probability:
                tiles[y][x] = CORRUPTED


# -------------------------------------------------------------- main
def generate(seed=None, width=32, height=24):
    if seed is None:
        seed = random.randint(1, 2**31 - 1)
    rng = random.Random(seed)

    tiles = [[VOID for _ in range(width)] for _ in range(height)]
    rooms = []

    # --- place rooms -----------------------------------------------
    target_rooms = 6
    for _attempt in range(target_rooms * 25):
        if len(rooms) >= target_rooms:
            break
        tmpl = _weighted_choice(rng, ROOM_TEMPLATES)
        name_t, wmin, wmax, hmin, hmax, _w = tmpl
        w = rng.randint(wmin, wmax)
        h = rng.randint(hmin, hmax)
        if w + 4 > width or h + 4 > height:
            continue
        x = rng.randint(2, width - w - 2)
        y = rng.randint(2, height - h - 2)
        if _overlaps_any(x, y, w, h, rooms, pad=2):
            continue
        for dy in range(h):
            for dx in range(w):
                tiles[y + dy][x + dx] = FLOOR
        rooms.append(Room(
            id=len(rooms),
            template=name_t,
            name=f"{rng.choice(ROOM_PREFIXES)}_{name_t}",
            x=x, y=y, w=w, h=h,
        ))

    if len(rooms) < 3:
        raise RuntimeError(
            f"only {len(rooms)} rooms placed (need >=3); retry with another seed")

    # --- connect rooms ---------------------------------------------
    for i in range(1, len(rooms)):
        prev = rooms[rng.randint(0, i - 1)]
        _carve_corridor(
            tiles,
            prev.x + prev.w // 2, prev.y + prev.h // 2,
            rooms[i].x + rooms[i].w // 2, rooms[i].y + rooms[i].h // 2,
        )

    # --- tier by distance from spawn -------------------------------
    spawn_room = rooms[0]
    sx0, sy0 = spawn_room.x + spawn_room.w * 0.5, spawn_room.y + spawn_room.h * 0.5
    for r in rooms:
        cx, cy = r.x + r.w * 0.5, r.y + r.h * 0.5
        dist = ((cx - sx0) ** 2 + (cy - sy0) ** 2) ** 0.5
        if dist > 12:
            r.tier = 3
        elif dist > 6:
            r.tier = 2
        else:
            r.tier = 1

    # --- terminals -----------------------------------------------
    terminals = []
    for i, r in enumerate(rooms):
        tx = r.x + r.w // 2
        ty = r.y + r.h // 2
        if tiles[ty][tx] != FLOOR:
            tiles[ty][tx] = FLOOR
        if i == 0:
            kind, prompt, req = "lore", *rng.choice(TERMINAL_PROMPTS["lore"])
        else:
            kind, prompt, req = "repair", *rng.choice(TERMINAL_PROMPTS["repair"])
        t = Terminal(
            id=len(terminals), room_id=r.id,
            x=tx, y=ty,
            kind=kind, prompt=prompt, requires_constructs=req,
        )
        r.terminal_id = t.id
        terminals.append(t)

    # --- corruption -----------------------------------------------
    for r in rooms[1:]:
        if rng.random() < 0.6:
            r.corrupted = True
            _corrupt_room(tiles, r, rng)

    # --- spawn -----------------------------------------------------
    spawn_x = spawn_room.x + spawn_room.w // 2
    spawn_y = spawn_room.y + spawn_room.h // 2
    if tiles[spawn_y][spawn_x] != FLOOR:
        tiles[spawn_y][spawn_x] = FLOOR

    # --- cull unreachable -----------------------------------------
    _cull_unreachable(tiles, spawn_x, spawn_y, width, height)

    # --- walls -----------------------------------------------------
    _apply_walls(tiles, width, height)

    return WorldMap(
        seed=seed, width=width, height=height,
        spawn_x=spawn_x, spawn_y=spawn_y,
        tiles=tiles, rooms=rooms, terminals=terminals,
    )
