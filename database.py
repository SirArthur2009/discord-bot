import os
import sqlite3
from datetime import datetime, timezone


DB_PATH = os.getenv("DB_PATH", "claims.db")


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = db_connect()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL UNIQUE,
        name TEXT NOT NULL,
        message TEXT NOT NULL,
        locked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS claim_options (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        claim_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        claimed_by INTEGER,
        claimed_at TEXT,
        UNIQUE(claim_id, name),
        FOREIGN KEY(claim_id) REFERENCES claims(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS option_interests (
        option_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        interested_at TEXT NOT NULL,
        PRIMARY KEY (option_id, user_id),
        FOREIGN KEY(option_id) REFERENCES claim_options(id) ON DELETE CASCADE
    );
    """)

    # Migrate claims created before they could be locked.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(claims)")}
    if "locked" not in columns:
        conn.execute("ALTER TABLE claims ADD COLUMN locked INTEGER NOT NULL DEFAULT 0")

    # Keep sign-ups created before the non-exclusive interest system.
    conn.execute(
        """
        INSERT OR IGNORE INTO option_interests (option_id, user_id, interested_at)
        SELECT claim_options.id, claim_options.claimed_by,
               COALESCE(claim_options.claimed_at, claims.created_at)
        FROM claim_options
        JOIN claims ON claims.id = claim_options.claim_id
        WHERE claim_options.claimed_by IS NOT NULL
        """
    )
    conn.commit()
    conn.close()


def get_claim_by_id(claim_id):
    conn = db_connect()
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    conn.close()
    return row


def get_claim_by_name(guild_id, name):
    conn = db_connect()
    row = conn.execute(
        "SELECT * FROM claims WHERE guild_id = ? AND name = ?",
        (guild_id, name),
    ).fetchone()
    conn.close()
    return row


def get_claims_by_guild(guild_id):
    conn = db_connect()
    rows = conn.execute(
        "SELECT * FROM claims WHERE guild_id = ? ORDER BY created_at, id", (guild_id,)
    ).fetchall()
    conn.close()
    return rows


def get_all_claim_ids():
    conn = db_connect()
    rows = conn.execute("SELECT id FROM claims").fetchall()
    conn.close()
    return rows


def get_options(claim_id):
    conn = db_connect()
    rows = conn.execute(
        "SELECT * FROM claim_options WHERE claim_id = ? ORDER BY id",
        (claim_id,),
    ).fetchall()
    conn.close()
    return rows


def get_interests(claim_id):
    conn = db_connect()
    rows = conn.execute(
        """
        SELECT option_id, user_id
        FROM option_interests
        WHERE option_id IN (
            SELECT id FROM claim_options WHERE claim_id = ?
        )
        ORDER BY interested_at, user_id
        """,
        (claim_id,),
    ).fetchall()
    conn.close()

    interests = {}
    for row in rows:
        interests.setdefault(row["option_id"], []).append(row["user_id"])
    return interests


def create_claim(guild_id, channel_id, name, message, option_names):
    conn = db_connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO claims
            (guild_id, channel_id, message_id, name, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, channel_id, 0, name, message, datetime.now(timezone.utc).isoformat()),
        )
        claim_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO claim_options (claim_id, name) VALUES (?, ?)",
            [(claim_id, option_name) for option_name in option_names],
        )
        conn.commit()
        return claim_id
    finally:
        conn.close()


def set_claim_message_id(claim_id, message_id):
    conn = db_connect()
    conn.execute("UPDATE claims SET message_id = ? WHERE id = ?", (message_id, claim_id))
    conn.commit()
    conn.close()


def delete_claim(claim_id):
    conn = db_connect()
    conn.execute("DELETE FROM claims WHERE id = ?", (claim_id,))
    conn.commit()
    conn.close()


def reset_claim(claim_id):
    conn = db_connect()
    try:
        conn.execute(
            """
            DELETE FROM option_interests
            WHERE option_id IN (
                SELECT id FROM claim_options WHERE claim_id = ?
            )
            """,
            (claim_id,),
        )
        # Clear values retained only for backwards compatibility with old boards.
        conn.execute(
            "UPDATE claim_options SET claimed_by = NULL, claimed_at = NULL WHERE claim_id = ?",
            (claim_id,),
        )
        conn.commit()
    finally:
        conn.close()


def lock_claim(claim_id):
    conn = db_connect()
    try:
        conn.execute("UPDATE claims SET locked = 1 WHERE id = ?", (claim_id,))
        conn.commit()
    finally:
        conn.close()


def toggle_interest(option_id, user_id):
    """Add or remove a member's interest in an option, atomically."""
    conn = db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        option = conn.execute(
            "SELECT * FROM claim_options WHERE id = ?", (option_id,)
        ).fetchone()
        if option is None:
            conn.rollback()
            return None, "missing"

        claim = conn.execute(
            "SELECT locked FROM claims WHERE id = ?", (option["claim_id"],)
        ).fetchone()
        if claim is None or claim["locked"]:
            conn.rollback()
            return option, "locked"

        existing = conn.execute(
            "SELECT 1 FROM option_interests WHERE option_id = ? AND user_id = ?",
            (option_id, user_id),
        ).fetchone()
        if existing is not None:
            conn.execute(
                "DELETE FROM option_interests WHERE option_id = ? AND user_id = ?",
                (option_id, user_id),
            )
            conn.commit()
            return option, "unattended"

        conn.execute(
            """
            INSERT INTO option_interests (option_id, user_id, interested_at)
            VALUES (?, ?, ?)
            """,
            (option_id, user_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return option, "interested"
    finally:
        conn.close()
