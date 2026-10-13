"""Persistent GitHub relay state, separate from existing claim boards."""
import json
import time
from contextlib import closing

from database import db_connect


def init_github_db():
    with closing(db_connect()) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS github_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            delivery TEXT NOT NULL UNIQUE,
            event TEXT NOT NULL,
            payload TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            retry_at REAL NOT NULL DEFAULT 0,
            repo TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS github_threads (
            repo TEXT NOT NULL,
            number INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            thread_id INTEGER,
            PRIMARY KEY (repo, number)
        );
        CREATE TABLE IF NOT EXISTS github_comments (
            repo TEXT NOT NULL,
            comment_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (repo, comment_id),
            UNIQUE (repo, message_id)
        );
        CREATE TABLE IF NOT EXISTS github_links (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            login TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS github_auth (
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            login TEXT NOT NULL, credentials TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS github_device_flows (
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            credentials TEXT NOT NULL, expires_at REAL NOT NULL,
            next_poll REAL NOT NULL, interval REAL NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS github_login_prompts (
            delivery TEXT PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS github_notification_preferences (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            login_reminders INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS github_comment_actors (
            repo TEXT NOT NULL, message_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            PRIMARY KEY (repo, message_id)
        );
        CREATE TABLE IF NOT EXISTS github_deadlines (
            repo TEXT NOT NULL,
            number INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL,
            due_at TEXT NOT NULL,
            PRIMARY KEY (repo, number)
        );
        CREATE TABLE IF NOT EXISTS github_reminders (
            repo TEXT NOT NULL,
            number INTEGER NOT NULL,
            due_at TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            stage TEXT NOT NULL,
            PRIMARY KEY (repo, number, due_at, user_id, stage)
        );
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(github_jobs)")}
        if "repo" not in columns:
            conn.execute("ALTER TABLE github_jobs ADD COLUMN repo TEXT NOT NULL DEFAULT ''")
            conn.commit()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(github_comments)")}
        for name in ("actor_user_id", "actor_guild_id"):
            if name not in columns:
                conn.execute(f"ALTER TABLE github_comments ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def execute(sql, params=(), *, one=False, rows=False):
    with closing(db_connect()) as conn:
        with conn:
            cursor = conn.execute(sql, params)
            if one:
                return cursor.fetchone()
            if rows:
                return cursor.fetchall()


def migrate_repo_scope(repo, scoped_repo):
    """Move legacy single-channel state to the explicitly matching configured channel."""
    with closing(db_connect()) as conn:
        with conn:
            for table in ("github_jobs", "github_threads", "github_comments", "github_deadlines", "github_reminders"):
                conn.execute(f"UPDATE OR IGNORE {table} SET repo = ? WHERE repo = ?", (scoped_repo, repo))


def enqueue(delivery, event, payload, repo=""):
    execute("INSERT OR IGNORE INTO github_jobs (delivery, event, payload, repo) VALUES (?, ?, ?, ?)",
            (delivery, event, json.dumps(payload), repo))


def next_job(repo=None):
    if repo is None:
        return execute("SELECT * FROM github_jobs WHERE done = 0 AND retry_at <= ? ORDER BY id LIMIT 1",
                       (time.time(),), one=True)
    return execute("SELECT * FROM github_jobs WHERE done = 0 AND retry_at <= ? AND repo = ? ORDER BY id LIMIT 1",
                   (time.time(), repo), one=True)


def finish_job(job_id):
    # Retain delivery IDs for duplicate webhook detection; discard potentially large bodies.
    execute("UPDATE github_jobs SET done = 1, payload = '{}' WHERE id = ?", (job_id,))


def retry_job(job, delay=None):
    delay = delay or min(3600, 5 * 2 ** min(job["attempts"], 10))
    execute("UPDATE github_jobs SET attempts = attempts + 1, retry_at = ? WHERE id = ?",
            (time.time() + delay, job["id"]))


def get_thread(repo, number):
    return execute("SELECT * FROM github_threads WHERE repo = ? AND number = ?", (repo, number), one=True)


def get_thread_by_id(repo, thread_id):
    return execute("SELECT * FROM github_threads WHERE repo = ? AND thread_id = ?", (repo, thread_id), one=True)


def get_thread_by_message(repo, message_id):
    return execute("SELECT * FROM github_threads WHERE repo = ? AND message_id = ?", (repo, message_id), one=True)


def pending_discord_comment(repo, message_id):
    return execute("SELECT * FROM github_jobs WHERE delivery = ? AND repo = ? AND done = 0",
                   (f"discord:{message_id}", repo), one=True)


def save_thread(repo, number, message_id, thread_id=None):
    execute("""INSERT INTO github_threads VALUES (?, ?, ?, ?)
               ON CONFLICT(repo, number) DO UPDATE SET message_id = excluded.message_id,
               thread_id = excluded.thread_id""", (repo, number, message_id, thread_id))


def get_comment(repo, comment_id):
    return execute("SELECT * FROM github_comments WHERE repo = ? AND comment_id = ?",
                   (repo, comment_id), one=True)


def get_comment_by_message(repo, message_id):
    return execute("SELECT * FROM github_comments WHERE repo = ? AND message_id = ?",
                   (repo, message_id), one=True)


def save_comment(repo, comment_id, message_id, source, actor_user_id=0, actor_guild_id=0):
    execute("""INSERT OR REPLACE INTO github_comments
               (repo, comment_id, message_id, source, actor_user_id, actor_guild_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (repo, comment_id, message_id, source, actor_user_id, actor_guild_id))


def get_link(guild_id, user_id):
    return execute("SELECT login FROM github_links WHERE guild_id = ? AND user_id = ?",
                   (guild_id, user_id), one=True)


def save_link(guild_id, user_id, login):
    execute("""INSERT INTO github_links VALUES (?, ?, ?)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET login = excluded.login""",
            (guild_id, user_id, login))


def delete_link(guild_id, user_id):
    execute("DELETE FROM github_links WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))


def login_reminders_enabled(guild_id, user_id):
    row = execute("SELECT login_reminders FROM github_notification_preferences WHERE guild_id = ? AND user_id = ?",
                  (guild_id, user_id), one=True)
    return row is None or bool(row["login_reminders"])


def set_login_reminders(guild_id, user_id, enabled):
    execute("""INSERT INTO github_notification_preferences VALUES (?, ?, ?)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET login_reminders = excluded.login_reminders""",
            (guild_id, user_id, int(enabled)))


def get_deadline(repo, number):
    return execute("SELECT * FROM github_deadlines WHERE repo = ? AND number = ?", (repo, number), one=True)


def save_deadline(repo, number, guild_id, recipient_id, due_at):
    with closing(db_connect()) as conn:
        with conn:
            conn.execute("DELETE FROM github_reminders WHERE repo = ? AND number = ?", (repo, number))
            conn.execute("""INSERT INTO github_deadlines VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(repo, number) DO UPDATE SET guild_id = excluded.guild_id,
                            recipient_id = excluded.recipient_id, due_at = excluded.due_at""",
                         (repo, number, guild_id, recipient_id, due_at))


def delete_deadline(repo, number):
    execute("DELETE FROM github_deadlines WHERE repo = ? AND number = ?", (repo, number))


def all_deadlines(repo):
    return execute("SELECT * FROM github_deadlines WHERE repo = ?", (repo,), rows=True)


def reminder_sent(deadline, user_id, stage):
    return execute("""SELECT 1 FROM github_reminders
                      WHERE repo = ? AND number = ? AND due_at = ? AND user_id = ? AND stage = ?""",
                   (deadline["repo"], deadline["number"], deadline["due_at"], user_id, stage), one=True)


def mark_reminder(deadline, user_id, stage):
    execute("INSERT OR IGNORE INTO github_reminders VALUES (?, ?, ?, ?, ?)",
            (deadline["repo"], deadline["number"], deadline["due_at"], user_id, stage))


def linked_assignees(guild_id, logins):
    rows = execute("SELECT user_id, login FROM github_links WHERE guild_id = ?", (guild_id,), rows=True)
    names = {name.casefold() for name in logins}
    return {row["user_id"] for row in rows if row["login"].casefold() in names}
