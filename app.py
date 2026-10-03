import sqlite3
import sys

class QueryGameEngine:
    def __init__(self, db_name="query_world.db"):
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self.setup_database()

    def setup_database(self):
        """Initializes the base sector schema and active hostile entities."""
        self.cursor.executescript("""
            CREATE TABLE IF NOT EXISTS hostile_mobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                hp INTEGER NOT NULL,
                armor_class INTEGER NOT NULL,
                elemental_weakness TEXT NOT NULL,
                position_x INTEGER,
                position_y INTEGER
            );

            CREATE TABLE IF NOT EXISTS conduits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zone TEXT NOT NULL,
                status TEXT NOT NULL,
                voltage INTEGER
            );

            -- Seed initial data
            DELETE FROM hostile_mobs;
            INSERT INTO hostile_mobs (name, hp, armor_class, elemental_weakness, position_x, position_y) VALUES
            ('Rogue_Daemon_v1', 50, 12, 'LIGHTNING', 3, 4),
            ('Glitch_Crawler', 30, 8, 'FIRE', 7, 2);

            DELETE FROM conduits;
            INSERT INTO conduits (zone, status, voltage) VALUES
            ('sector_7', 'frayed', 220),
            ('sub_basement', 'active', 110);
        """)
        self.conn.commit()

    def cast_spell(self, query_string):
        """Evaluates player SQL input as a dynamic spell execution."""
        try:
            self.cursor.execute(query_string)
            if query_string.strip().upper().startswith("SELECT"):
                rows = self.cursor.fetchall()
                print("\n[SPELL EXECUTED SUCCESSFULLY]")
                for row in rows:
                    print(f"  -> Result: {row}")
            else:
                self.conn.commit()
                print("\n[MUTATION SPELL APPLIED: State Updated]")
        except sqlite3.Error as e:
            print(f"\n[SYNTAX ERROR] Execution Fizzled: {e}")

    def run(self):
        print("=== QUERY: THE QUERY THAT SAVED THE WORLD ===")
        print("Type your SQL spell below (or type 'EXIT' to quit).\n")
        
        while True:
            try:
                user_input = input("QUERY> ").strip()
                if user_input.upper() == 'EXIT':
                    break
                if not user_input:
                    continue
                self.cast_spell(user_input)
            except KeyboardInterrupt:
                break

        self.conn.close()
        print("\nSession terminated.")

if __name__ == "__main__":
    game = QueryGameEngine()
    game.run()
