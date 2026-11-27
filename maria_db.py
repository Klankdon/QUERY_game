"""STAGING realm -- MariaDB 5.7+ semantics, simulated on a SQLite backend.

MariaDB defaults enforced pre-execution:
  * ONLY_FULL_GROUP_BY     -- rejects non-aggregated SELECT columns
  * STRICT_TRANS_TABLES    -- rejects non-numeric strings in INTEGER columns
  * `||` reads as OR, not CONCAT (unless PIPES_AS_CONCAT is set)

MARIADB_DDL_REAL documents the physical schema this realm would use in a
live deployment. v0.5 uses SQLITE_DDL (inherited) because the simulated
backend is SQLite. A live pymysql connection replaces both `connect()` and
`DDL` -- no other changes needed.
"""
from db import StrictRules
from sqlite_db import SQLiteAdapter, SQLITE_DDL

MARIADB_DDL_REAL = """
CREATE TABLE hostile_mobs (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(255) NOT NULL,
    hp INT NOT NULL,
    armor_class INT NOT NULL,
    elemental_weakness VARCHAR(64) NOT NULL,
    position_x INT,
    position_y INT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE conduits (
    id INT PRIMARY KEY AUTO_INCREMENT,
    zone VARCHAR(255) NOT NULL,
    status VARCHAR(64) NOT NULL,
    voltage INT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE inventory (
    id INT PRIMARY KEY AUTO_INCREMENT,
    item_name VARCHAR(255) NOT NULL,
    rarity VARCHAR(64) NOT NULL,
    slot_type VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE player_state (
    id INT PRIMARY KEY CHECK (id = 1),
    handle VARCHAR(255) NOT NULL,
    level INT NOT NULL DEFAULT 1,
    hp INT NOT NULL,
    max_hp INT NOT NULL,
    mana INT NOT NULL,
    max_mana INT NOT NULL,
    position_x INT NOT NULL,
    position_y INT NOT NULL,
    zone_tier INT NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE spellbook (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(255) NOT NULL,
    query TEXT NOT NULL,
    mana_cost INT NOT NULL,
    hotbar_slot INT UNIQUE,
    times_cast INT NOT NULL DEFAULT 0,
    dialect_written VARCHAR(32) NOT NULL DEFAULT 'mariadb',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


class MariaDBAdapter(SQLiteAdapter):
    name, dialect, tier = "STAGING", "mariadb", 2
    rules = StrictRules(only_full_group_by=True, strict_types=True)
    DDL   = SQLITE_DDL        # simulated; live swap -> MARIADB_DDL_REAL

    def __init__(self, file_path="query_maria.db"):
        super().__init__(file_path)
