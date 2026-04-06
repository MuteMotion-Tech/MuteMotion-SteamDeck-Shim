# =============================================================================
# MuteMotion Settings Database — SQLite3 Persistence Layer
# =============================================================================
# simple key-value store using python's built-in sqlite3 module.
# stores user preferences (preset, intensity, opacity, invert_axis) so they
# survive plugin reloads and device reboots. no external dependencies.
#
# schema:
#   CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)
#
# the value column stores everything as TEXT. callers are responsible for
# type conversion (float(), bool(), etc). this is intentional — sqlite3's
# type affinity system is more trouble than it's worth for 4 settings.
# =============================================================================

import sqlite3
import os
import logging

# default settings — applied on first run or when user hits "Reset to Defaults"
DEFAULTS = {
    "preset": "dotgrid",
    "intensity": "0.5",
    "opacity": "0.8",
    "invert_axis": "true",
}

class SettingsDB:
    def __init__(self, db_path: str):
        """
        Initialize the settings database.
        Creates the table and populates defaults if the DB doesn't exist yet.
        """
        self._db_path = db_path
        self._ensure_directory()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")   # safer for concurrent reads
        self._create_table()
        self._populate_defaults()
        logging.info(f"[SettingsDB] Initialized at {db_path}")

    def _ensure_directory(self):
        """make sure the parent directory exists"""
        parent = os.path.dirname(self._db_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

    def _create_table(self):
        """create the settings table if it doesn't exist"""
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        self._conn.commit()

    def _populate_defaults(self):
        """insert default values for any keys that don't already exist"""
        for key, value in DEFAULTS.items():
            self._conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value)
            )
        self._conn.commit()

    def get(self, key: str, default: str = "") -> str:
        """retrieve a setting value by key. returns default if not found."""
        cursor = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return row[0] if row else default

    def get_float(self, key: str, default: float = 0.0) -> float:
        """convenience: retrieve a setting as a float"""
        try:
            return float(self.get(key, str(default)))
        except (ValueError, TypeError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """convenience: retrieve a setting as a boolean"""
        val = self.get(key, str(default)).lower()
        return val in ("true", "1", "yes")

    def set(self, key: str, value) -> None:
        """save a setting. converts value to string for storage."""
        str_value = str(value).lower() if isinstance(value, bool) else str(value)
        self._conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str_value)
        )
        self._conn.commit()

    def get_all(self) -> dict:
        """return all settings as a typed dictionary (for the get_settings RPC)"""
        return {
            "preset": self.get("preset", DEFAULTS["preset"]),
            "intensity": self.get_float("intensity", 0.5),
            "opacity": self.get_float("opacity", 0.8),
            "invert_axis": self.get_bool("invert_axis", True),
        }

    def reset_all(self) -> dict:
        """wipe all settings back to defaults. returns the default values."""
        self._conn.execute("DELETE FROM settings")
        self._conn.commit()
        self._populate_defaults()
        logging.info("[SettingsDB] All settings reset to defaults")
        return self.get_all()

    def close(self):
        """close the database connection"""
        if self._conn:
            self._conn.close()
            logging.info("[SettingsDB] Connection closed")
