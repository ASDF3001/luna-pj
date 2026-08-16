import os
import sqlite3
import time

DB_PATH = os.getenv("ANTI_BOT_DB", "anti_bot.sqlite3")

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row

with db:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id INTEGER PRIMARY KEY,
        anti_raid_count INTEGER,
        anti_raid_punish TEXT,
        anti_spam_count INTEGER,
        anti_spam_punish TEXT,
        anti_url_count INTEGER,
        anti_url_treatment TEXT,
        auth_channel_id INTEGER,
        auth_role_id INTEGER,
        auth_mode TEXT,
        auth_title TEXT,
        auth_body TEXT,
        auth_button_label TEXT,
        ticket_channel_id INTEGER,
        ticket_role_id INTEGER,
        ticket_title TEXT,
        ticket_body TEXT,
        ticket_open_text TEXT,
        trap_channel_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS whitelist (
        guild_id INTEGER,
        user_id INTEGER,
        PRIMARY KEY (guild_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS blacklist (
        guild_id INTEGER,
        user_id INTEGER,
        PRIMARY KEY (guild_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS auth_codes (
        guild_id INTEGER,
        user_id INTEGER,
        code TEXT,
        expires REAL,
        PRIMARY KEY (guild_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS auth_attempts (
        user_id INTEGER PRIMARY KEY,
        count INTEGER DEFAULT 0,
        first_attempt REAL
    );

    CREATE TABLE IF NOT EXISTS ticket_cooldowns (
        guild_id INTEGER,
        user_id INTEGER,
        created_at REAL,
        PRIMARY KEY (guild_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS role_panels (
        message_id INTEGER PRIMARY KEY,
        guild_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        mode TEXT NOT NULL,
        title TEXT,
        body TEXT
    );

    CREATE TABLE IF NOT EXISTS role_panel_roles (
        message_id INTEGER NOT NULL,
        role_id INTEGER NOT NULL,
        label TEXT,
        PRIMARY KEY (message_id, role_id)
    );

    CREATE TABLE IF NOT EXISTS economy (
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        balance INTEGER NOT NULL DEFAULT 0,
        last_daily TEXT,
        last_login TEXT,
        login_streak INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS shop_items (
        guild_id INTEGER NOT NULL,
        item_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        price INTEGER NOT NULL,
        description TEXT,
        PRIMARY KEY (guild_id, item_id)
    );

    CREATE TABLE IF NOT EXISTS inventory (
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        item_id INTEGER NOT NULL,
        qty INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id, item_id)
    );

    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        from_id INTEGER NOT NULL,
        to_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        reason TEXT,
        created_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS game_stats (
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        game TEXT NOT NULL,
        plays INTEGER NOT NULL DEFAULT 0,
        wins INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id, game)
    );
    """)

_NEW_GUILD_COLUMNS = {
    "anti_nuke_channel_count": "INTEGER",
    "anti_nuke_kick_count": "INTEGER",
    "anti_nuke_ban_count": "INTEGER",
    "anti_nuke_window": "INTEGER",
    "anti_nuke_punish": "TEXT",
    "economy_enabled": "INTEGER",
    "daily_amount": "INTEGER",
    "login_bonus": "INTEGER",
}


def _ensure_columns():
    existing = {
        row["name"]
        for row in db.execute("PRAGMA table_info(guild_settings)").fetchall()
    }
    with db:
        for name, col_type in _NEW_GUILD_COLUMNS.items():
            if name not in existing:
                db.execute(
                    f"ALTER TABLE guild_settings ADD COLUMN {name} {col_type}"
                )
    econ_cols = {
        row["name"]
        for row in db.execute("PRAGMA table_info(economy)").fetchall()
    }
    if "last_mission" not in econ_cols:
        with db:
            db.execute("ALTER TABLE economy ADD COLUMN last_mission TEXT")


_ensure_columns()

VALID_PUNISH = {"kick", "ban", "timeout"}
VALID_TREATMENT = {"delete", "kick", "ban", "timeout"}
VALID_NUKE_PUNISH = {"timeout", "kick", "ban", "none"}
AUTH_MAX_ATTEMPTS = 5
AUTH_LOCKOUT_SEC = 600
TICKET_COOLDOWN_SEC = 300
PURGE_MAX = 100
DEFAULT_NUKE_WINDOW = 10
DEFAULT_DAILY = 100
DEFAULT_LOGIN_BONUS = 50


def ensure_guild(guild_id: int):
    with db:
        db.execute(
            """INSERT OR IGNORE INTO guild_settings
            (guild_id, anti_raid_count, anti_raid_punish,
             anti_spam_count, anti_spam_punish,
             anti_url_count, anti_url_treatment,
             auth_mode, auth_title, auth_body, auth_button_label,
             ticket_title, ticket_body, ticket_open_text,
             anti_nuke_window, anti_nuke_punish,
             economy_enabled, daily_amount, login_bonus)
             VALUES (?, NULL, 'timeout', NULL, 'timeout', NULL, 'delete',
                     'button', '認証', 'ボタンを押して認証してください。',
                     '認証する', 'チケット作成', '下のボタンからチケットを作成できます。',
                     'チケットを作成しました。',
                     10, 'timeout', 1, 100, 50)""",
            (guild_id,),
        )


def get_settings(guild_id: int):
    ensure_guild(guild_id)
    return db.execute(
        "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
    ).fetchone()


def update_settings(guild_id: int, **values):
    ensure_guild(guild_id)
    if not values:
        return
    columns = ", ".join(f"{k} = ?" for k in values)
    params = list(values.values()) + [guild_id]
    with db:
        db.execute(f"UPDATE guild_settings SET {columns} WHERE guild_id = ?", params)


def is_whitelisted(guild_id: int, user_id: int) -> bool:
    return db.execute(
        "SELECT 1 FROM whitelist WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    ).fetchone() is not None


def is_blacklisted(guild_id: int, user_id: int) -> bool:
    return db.execute(
        "SELECT 1 FROM blacklist WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    ).fetchone() is not None


def check_auth_attempts(user_id: int) -> bool:
    """試行回数が上限に達していたらTrue"""
    row = db.execute(
        "SELECT count, first_attempt FROM auth_attempts WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if not row:
        return False
    if time.time() - row["first_attempt"] > AUTH_LOCKOUT_SEC:
        with db:
            db.execute("DELETE FROM auth_attempts WHERE user_id=?", (user_id,))
        return False
    return row["count"] >= AUTH_MAX_ATTEMPTS


def record_auth_attempt(user_id: int):
    row = db.execute(
        "SELECT count, first_attempt FROM auth_attempts WHERE user_id=?",
        (user_id,),
    ).fetchone()
    now = time.time()
    if not row or now - row["first_attempt"] > AUTH_LOCKOUT_SEC:
        with db:
            db.execute(
                "INSERT OR REPLACE INTO auth_attempts VALUES (?, 1, ?)",
                (user_id, now),
            )
    else:
        with db:
            db.execute(
                "UPDATE auth_attempts SET count = count + 1 WHERE user_id=?",
                (user_id,),
            )


def clear_auth_attempts(user_id: int):
    with db:
        db.execute("DELETE FROM auth_attempts WHERE user_id=?", (user_id,))


def ensure_wallet(guild_id: int, user_id: int):
    with db:
        db.execute(
            "INSERT OR IGNORE INTO economy (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )


def get_wallet(guild_id: int, user_id: int):
    ensure_wallet(guild_id, user_id)
    return db.execute(
        "SELECT * FROM economy WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    ).fetchone()


def add_balance(guild_id: int, user_id: int, amount: int, reason: str,
                from_id: int = 0) -> int:
    ensure_wallet(guild_id, user_id)
    with db:
        db.execute(
            "UPDATE economy SET balance = balance + ? WHERE guild_id=? AND user_id=?",
            (amount, guild_id, user_id),
        )
        db.execute(
            """INSERT INTO transactions
               (guild_id, from_id, to_id, amount, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (guild_id, from_id, user_id, amount, reason, time.time()),
        )
    return get_wallet(guild_id, user_id)["balance"]


def transfer_balance(guild_id: int, from_id: int, to_id: int,
                     amount: int, reason: str) -> str:
    if amount <= 0:
        return "invalid"
    if from_id == to_id:
        return "self"
    ensure_wallet(guild_id, from_id)
    ensure_wallet(guild_id, to_id)
    src = get_wallet(guild_id, from_id)
    if src["balance"] < amount:
        return "broke"
    with db:
        db.execute(
            "UPDATE economy SET balance = balance - ? WHERE guild_id=? AND user_id=?",
            (amount, guild_id, from_id),
        )
        db.execute(
            "UPDATE economy SET balance = balance + ? WHERE guild_id=? AND user_id=?",
            (amount, guild_id, to_id),
        )
        db.execute(
            """INSERT INTO transactions
               (guild_id, from_id, to_id, amount, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (guild_id, from_id, to_id, amount, reason, time.time()),
        )
    return "ok"


def record_game(guild_id: int, user_id: int, game: str, win: bool):
    with db:
        db.execute(
            """INSERT INTO game_stats (guild_id, user_id, game, plays, wins)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(guild_id, user_id, game) DO UPDATE SET
                 plays = plays + 1,
                 wins = wins + excluded.wins""",
            (guild_id, user_id, game, 1 if win else 0),
        )
