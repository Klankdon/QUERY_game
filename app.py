import pygame
import sqlite3
import sys
import os
import random

# Initialize Pygame
pygame.init()

# Window Configuration
SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 720
FPS = 60

# Cyberpunk Color Palette (CRT Green & Neon accents)
COLOR_BG = (10, 12, 10)
COLOR_GRID = (20, 35, 20)
COLOR_TEXT = (50, 255, 50)
COLOR_MUTED = (30, 100, 30)
COLOR_ACCENT = (0, 255, 200)
COLOR_DANGER = (255, 50, 50)
COLOR_PANEL = (15, 20, 15)

class QueryGameEngine:
    def __init__(self):
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption("QUERY: The Query That Saved The World [Dev Build]")
        self.clock = pygame.time.Clock()
        
        # Fonts
        self.font_large = pygame.font.SysFont("Courier New", 20, bold=True)
        self.font_small = pygame.font.SysFont("Courier New", 14)
        
        # Database setup for game state & live spell casting
        self.db_name = "query_world.db"
        if os.path.exists(self.db_name):
            os.remove(self.db_name) # Fresh state on launch for testing
            
        self.conn = sqlite3.connect(self.db_name)
        self.cursor = self.conn.cursor()
        self.init_world_database()
        
        # Player & Input State
        self.player_x = 5
        self.player_y = 5
        self.current_zone_tier = 1
        self.current_input = ""
        self.console_history = [
            "[SYSTEM] QUERY OS v1.0.4 initialized.",
            "[SYSTEM] Connected to local sector SQLite database.",
            "[SYSTEM] Type SQL spells in console below (e.g., SELECT * FROM hostile_mobs);"
        ]
        
        # Load Assets
        self.load_assets()

    def init_world_database(self):
        """Initializes game tables, active hostiles, and sector grid schema."""
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

            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_name TEXT NOT NULL,
                rarity TEXT NOT NULL,
                slot_type TEXT NOT NULL
            );

            -- Seed data
            INSERT INTO hostile_mobs (name, hp, armor_class, elemental_weakness, position_x, position_y) VALUES
            ('Rogue_Daemon_v1', 50, 12, 'LIGHTNING', 8, 4),
            ('Glitch_Crawler', 30, 8, 'FIRE', 3, 7),
            ('Subnet_Stalker', 75, 15, 'COLD', 12, 2);

            INSERT INTO conduits (zone, status, voltage) VALUES
            ('sector_7_slums', 'frayed', 220),
            ('industrial_core', 'active', 440);
            
            INSERT INTO inventory (item_name, rarity, slot_type) VALUES
            ('Scrap CRT Deck Mk.I', 'Common', 'HUD'),
            ('Root Kit Fragment A', 'Legendary', 'ARMBAND');
        """)
        self.conn.commit()

    def load_assets(self):
        """Loads and scales pixel art or chibi sprite graphics."""
        self.player_img = None
        self.mob_img = None
        
        try:
            if os.path.exists("assets/chibi_char.png"):
                img = pygame.image.load("assets/chibi_char.png").convert_alpha()
                self.player_img = pygame.transform.scale(img, (32, 32))
        except pygame.error as e:
            print(f"[WARNING] Could not load player asset: {e}")

    def roll_loot_drop(self, zone_tier=1):
        """Generates a procedural loot drop scaled by zone tier (1: Basic, 2: Mid, 3: High)."""
        loot_table = [
            # Tier 1: Basic
            {"item": "Scrap CRT Deck Mk.I", "rarity": "Common", "slot": "HUD", "tier": 1, "weight": 50},
            {"item": "Frayed Copper Interface", "rarity": "Common", "slot": "CABLE", "tier": 1, "weight": 35},
            
            # Tier 2: Mid-Level
            {"item": "Overclocked Heat Sink", "rarity": "Uncommon", "slot": "MOD", "tier": 2, "weight": 12},
            {"item": "Shielded Subnet Cable", "rarity": "Uncommon", "slot": "CABLE", "tier": 2, "weight": 8},
            
            # Tier 3: High-Level
            {"item": "Subquery Processing Core", "rarity": "Rare", "slot": "HUD", "tier": 3, "weight": 3},
            {"item": "Black Hat Root Console", "rarity": "Epic", "slot": "DECK", "tier": 3, "weight": 1}
        ]
        
        available_loot = [item for item in loot_table if item["tier"] <= zone_tier]
        total_weight = sum(item["weight"] for item in available_loot)
        if total_weight == 0:
            return None
            
        roll = random.randint(1, total_weight)
        cumulative = 0
        for entry in available_loot:
            cumulative += entry["weight"]
            if roll <= cumulative:
                self.add_to_inventory(entry)
                self.console_history.append(f"[LOOT ACQUIRED] ({entry['rarity']}) {entry['item']}")
                return entry
                
        return None

    def add_to_inventory(self, item_data):
        """Inserts a dropped item into the SQLite inventory table."""
        self.cursor.execute(
            "INSERT INTO inventory (item_name, rarity, slot_type) VALUES (?, ?, ?)",
            (item_data["item"], item_data["rarity"], item_data["slot"])
        )
        self.conn.commit()

    def execute_spell(self, query_str):
        """Evaluates player SQL input as a dynamic spell execution against world state."""
        self.console_history.append(f"QUERY> {query_str}")
        try:
            self.cursor.execute(query_str)
            query_upper = query_str.strip().upper()
            
            if query_upper.startswith("SELECT"):
                rows = self.cursor.fetchall()
                if rows:
                    for row in rows:
                        self.console_history.append(f"  -> {row}")
                else:
                    self.console_history.append("  -> [SET EMPTY: 0 rows returned]")
            else:
                self.conn.commit()
                self.console_history.append("  -> [MUTATION APPLIED: Database state updated]")
        except sqlite3.Error as e:
            self.console_history.append(f"  -> [SYNTAX ERROR] Fizzled: {e}")
            
        if len(self.console_history) > 15:
            self.console_history.pop(0)

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    if self.current_input.strip():
                        self.execute_spell(self.current_input)
                        self.current_input = ""
                elif event.key == pygame.K_BACKSPACE:
                    self.current_input = self.current_input[:-1]
                # Player Grid Movement Controls (fixed to use player_y and player_x)
                elif event.key in (pygame.K_w, pygame.K_UP):
                    if self.player_y > 0:
                        self.player_y -= 1
                elif event.key in (pygame.K_s, pygame.K_DOWN):
                    if self.player_y < 11:
                        self.player_y += 1
                elif event.key in (pygame.K_a, pygame.K_LEFT):
                    if self.player_x > 0:
                        self.player_x -= 1
                elif event.key in (pygame.K_d, pygame.K_RIGHT):
                    if self.player_x < 15:
                        self.player_x += 1
                else:
                    if event.unicode.isprintable():
                        self.current_input += event.unicode
                        
        return True

    def draw_grid(self):
        grid_size = 40
        start_x = 40
        start_y = 40
        
        # Draw background grid lines
        for x in range(0, 16):
            for y in range(0, 12):
                rect = pygame.Rect(start_x + (x * grid_size), start_y + (y * grid_size), grid_size - 2, grid_size - 2)
                pygame.draw.rect(self.screen, COLOR_GRID, rect, 1)

        # Draw Player
        p_screen_x = start_x + (self.player_x * grid_size) + 4
        p_screen_y = start_y + (self.player_y * grid_size) + 4
        
        if self.player_img:
            self.screen.blit(self.player_img, (p_screen_x, p_screen_y))
        else:
            p_rect = pygame.Rect(p_screen_x, p_screen_y, grid_size - 12, grid_size - 12)
            pygame.draw.rect(self.screen, COLOR_ACCENT, p_rect)

        # Draw Hostile Entities from Database
        try:
            self.cursor.execute("SELECT position_x, position_y, hp FROM hostile_mobs WHERE hp > 0")
            mobs = self.cursor.fetchall()
            for mx, my, hp in mobs:
                m_screen_x = start_x + (mx * grid_size) + 4
                m_screen_y = start_y + (my * grid_size) + 4
                
                if self.mob_img:
                    self.screen.blit(self.mob_img, (m_screen_x, m_screen_y))
                else:
                    m_rect = pygame.Rect(m_screen_x, m_screen_y, grid_size - 18, grid_size - 18)
                    pygame.draw.rect(self.screen, COLOR_DANGER, m_rect)
        except sqlite3.Error:
            pass

    def draw_hud(self):
        """Renders the cyberpunk status panels and query terminal."""
        panel_rect = pygame.Rect(700, 20, 560, 680)
        pygame.draw.rect(self.screen, COLOR_PANEL, panel_rect)
        pygame.draw.rect(self.screen, COLOR_MUTED, panel_rect, 2)

        # Title
        title = self.font_large.render("=== QUERY SPELL WORKBENCH ===", True, COLOR_TEXT)
        self.screen.blit(title, (720, 40))

        # Render Console History
        y_offset = 80
        for line in self.console_history:
            txt = self.font_small.render(line, True, COLOR_TEXT if "->" not in line else COLOR_ACCENT)
            self.screen.blit(txt, (720, y_offset))
            y_offset += 25

        # Input Box
        input_bg = pygame.Rect(720, 630, 520, 40)
        pygame.draw.rect(self.screen, COLOR_BG, input_bg)
        pygame.draw.rect(self.screen, COLOR_TEXT, input_bg, 1)

        input_txt = self.font_small.render(f"QUERY> {self.current_input}_", True, COLOR_TEXT)
        self.screen.blit(input_txt, (735, 642))

    def run(self):
        running = True
        while running:
            running = self.handle_events()
            
            self.screen.fill(COLOR_BG)
            self.draw_grid()
            self.draw_hud()
            
            pygame.display.flip()
            self.clock.tick(FPS)

        self.conn.close()
        pygame.quit()
        sys.exit()

if __name__ == "__main__":
    game = QueryGameEngine()
    game.run()
