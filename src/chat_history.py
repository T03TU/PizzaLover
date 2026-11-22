"""Persistent local profiles and conversations, independent of model resources."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4


class ChatHistory:
    """Scope every conversation operation to its owning local profile."""

    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT UNIQUE NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES profiles(id),
                    title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chats_by_profile ON chats(profile_id, updated_at);
                CREATE TABLE IF NOT EXISTS turns (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE NOT NULL, chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    payload TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def profiles(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id, name FROM profiles ORDER BY name_key")]

    def profile(self, profile_id):
        with self.connect() as db:
            row = db.execute("SELECT id, name FROM profiles WHERE id = ?", (profile_id,)).fetchone()
            return dict(row) if row else None

    def create_profile(self, name):
        profile = {"id": str(uuid4()), "name": name}
        with self.connect() as db:
            db.execute("INSERT INTO profiles (id, name, name_key) VALUES (?, ?, ?)", (profile["id"], name, name.casefold()))
        return profile

    def chats(self, profile_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""
                SELECT chats.*, COUNT(turns.id) AS turn_count FROM chats
                LEFT JOIN turns ON turns.chat_id = chats.id
                WHERE profile_id = ? GROUP BY chats.id ORDER BY updated_at DESC
            """, (profile_id,))]

    def create_chat(self, profile_id, title):
        stamp = datetime.now(timezone.utc).isoformat()
        chat = {"id": str(uuid4()), "profile_id": profile_id, "title": title,
                "created_at": stamp, "updated_at": stamp}
        with self.connect() as db:
            db.execute("INSERT INTO chats VALUES (:id, :profile_id, :title, :created_at, :updated_at)", chat)
        return dict(chat, turns=[])

    def chat(self, profile_id, chat_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM chats WHERE id = ? AND profile_id = ?", (chat_id, profile_id)).fetchone()
            if not row:
                return None
            turns = [json.loads(turn["payload"]) for turn in db.execute(
                "SELECT payload FROM turns WHERE chat_id = ? ORDER BY sequence", (chat_id,))]
            return dict(row, turns=turns)

    def start_turn(self, profile_id, chat_id, question, filters, eval_enabled):
        """Save the query before generation so failed and stopped requests remain tracked."""
        stamp = datetime.now(timezone.utc).isoformat()
        turn = {"id": str(uuid4()), "question": question, "text": "", "citations": [],
                "metrics": None, "filters": filters, "eval_enabled": eval_enabled,
                "created_at": stamp, "status": "interrupted", "error": "This turn did not finish."}
        with self.connect() as db:
            if not db.execute("SELECT id FROM chats WHERE id = ? AND profile_id = ?", (chat_id, profile_id)).fetchone():
                raise KeyError(chat_id)
            db.execute("INSERT INTO turns (id, chat_id, payload) VALUES (?, ?, ?)",
                       (turn["id"], chat_id, json.dumps(turn, allow_nan=False)))
            db.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (stamp, chat_id))
        return turn

    def save_turn(self, profile_id, chat_id, turn):
        with self.connect() as db:
            db.execute("""UPDATE turns SET payload = ? WHERE id = ? AND chat_id = ?
                AND EXISTS (SELECT 1 FROM chats WHERE id = ? AND profile_id = ?)""",
                       (json.dumps(turn, allow_nan=False), turn["id"], chat_id, chat_id, profile_id))
            db.execute("UPDATE chats SET updated_at = ? WHERE id = ? AND profile_id = ?",
                       (datetime.now(timezone.utc).isoformat(), chat_id, profile_id))

    def delete_chat(self, profile_id, chat_id):
        with self.connect() as db:
            return db.execute("DELETE FROM chats WHERE id = ? AND profile_id = ?", (chat_id, profile_id)).rowcount > 0
