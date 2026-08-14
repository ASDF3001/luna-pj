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
    """)

VALID_PUNISH = {"kick", "ban", "timeout"}
VALID_TREATMENT = {"delete", "kick", "ban", "timeout"}
AUTH_MAX_ATTEMPTS = 5
AUTH_LOCKOUT_SEC = 600
TICKET_COOLDOWN_SEC = 300


def ensure_guild(guild_id: int):
    with db:
        db.execute(
            """INSERT OR IGNORE INTO guild_settings
            (guild_id, anti_raid_count, anti_raid_punish,
             anti_spam_count, anti_spam_punish,
             anti_url_count, anti_url_treatment,
             auth_mode, auth_title, auth_body, auth_button_label,
             ticket_title, ticket_body, ticket_open_text)
             VALUES (?, NULL, 'timeout', NULL, 'timeout', NULL, 'delete',
                     'button', '認証', 'ボタンを押して認証してください。',
                     '認証する', 'チケット作成', '下のボタンからチケットを作成できます。',
                     'チケットを作成しました。')""",
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
