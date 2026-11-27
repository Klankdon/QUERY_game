# QUERY: The Query That Saved The World

*A dystopian cyberpunk RPG where database management is magic.* Repair collapsing city infrastructures, collect rare hardware HUDs, assemble black-hat root kits, and cast custom SQL queries as spells.

## Current State (v0.6 — Multi-Realm Progression & Procedural World)

The engine is split into modular realm adapters with AST-level SQL curriculum gating (`sqlglot`), procedural 2.5D map generation, and cross-engine migration mechanics:

* **Three Realms & Dialects:**
  * **DEV (`sqlite`):** Permissive starting sandbox (`query_sqlite.db`).
  * **STAGING (`mariadb`):** Enforces `ONLY_FULL_GROUP_BY`, strict integer typing, and catches `||` concatenation vs. logical `OR` traps (`query_maria.db`).
  * **PROD (`postgres`):** Enforces strict grouping/typing and unlocks PostgreSQL-specific constructs like `ILIKE`, `DISTINCT ON`, and JSONB operators (`query_postgres.db`).
* **Procedural 2.5D World (`map_gen.py`):** Generates rooms, corridors, corrupted void tiles, and terminals stored directly in queryable SQL tables (`world_map`, `rooms`, `terminals`, `map_tiles`), rendered with a Y-sorted orthographic camera and click/drag pathfinding.
* **Spellbook & Hotbar (`1–9`):** Engrave working queries to hotbar slots `1–9` with dialect provenance (`dialect_written`) and cast them on demand.
* **Realm Auditing & Migration (`MIGRATE`):** At Level 5+, run `MIGRATE` to audit your spellbook for breaking constructs, strictness violations, and dialect hazards against the next realm, then execute `MIGRATE CONFIRM` to cross over with your player state and spellbook intact.

## Architecture

* `db.py` — Shared foundation: `CONSTRUCTS` curriculum, strictness rules, `EngineAdapter` base
* `sqlite_db.py` — DEV realm adapter, sandbox authorizer, and SQLite DDL
* `maria_db.py` — STAGING realm adapter (MariaDB 5.7+ semantics) & `MARIADB_DDL_REAL`
* `postgres_db.py` — PROD realm adapter (PostgreSQL semantics) & `POSTGRES_DDL_REAL`
* `map_gen.py` — Procedural 2.5D cyberpunk room/corridor/terminal generator
* `game.py` — Pygame loop, Y-sort renderer, mouse/keyboard pathing, HUD workbench, and CLI entry point
* `assets/` — Sprites (`chibi_char.png`, `chibi_mob.png`)
* `start.bat` / `start.sh` — Windows and Linux/macOS venv setup & launchers

## Getting Started Locally

* **Windows:** Run `start.bat` (or `start.bat --all` for full progression).
* **Linux / macOS:** Run `chmod +x start.sh && ./start.sh --all`.
* **CLI Flags:** `--realm dev|staging|prod`, `--all`, `--fresh`.

---

**Screenshots & Visual Roadmap:** View progress shots and concept art in the [QUERY Game Assets DeviantArt Gallery](https://www.deviantart.com/dragonsflyartstudio/gallery/103885995/query-game-assets).
