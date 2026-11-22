"""Persistent permissions, password credentials and opaque browser sessions."""
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

PERMISSIONS = ('chat:use', 'scorecards:view', 'feedback:submit', 'users:create',
               'pipeline:manage', 'roles:manage')
USER_PERMISSIONS = PERMISSIONS[:3]


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f'{salt}:{digest}'


def password_matches(password, encoded):
    return bool(encoded) and hmac.compare_digest(password_hash(password, encoded.split(':')[0]), encoded)


class Access:
    def __init__(self, history):
        self.history = history
        self.setup_token = secrets.token_urlsafe(32)
        self.dummy_hash = password_hash(secrets.token_hex(16))
        with history.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS roles (name TEXT PRIMARY KEY COLLATE NOCASE);
                CREATE TABLE IF NOT EXISTS role_permissions (
                    role TEXT NOT NULL REFERENCES roles(name) ON DELETE CASCADE,
                    permission TEXT NOT NULL, PRIMARY KEY(role, permission));
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES profiles(id),
                    expires_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS response_feedback (
                    id TEXT PRIMARY KEY, message_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    role TEXT NOT NULL, rating TEXT NOT NULL CHECK(rating IN ('thumbs_up','thumbs_down')),
                    comment TEXT NOT NULL, timestamp TEXT NOT NULL,
                    UNIQUE(message_id, user_id));
            ''')
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(profiles)')}
            migrating = 'role' not in columns
            if migrating:
                db.execute("ALTER TABLE profiles ADD COLUMN role TEXT NOT NULL DEFAULT 'USER'")
                db.execute('ALTER TABLE profiles ADD COLUMN password_hash TEXT')
                db.execute('ALTER TABLE profiles ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
            for role, permissions in [('ADMIN', PERMISSIONS), ('USER', USER_PERMISSIONS)]:
                if not db.execute('SELECT 1 FROM roles WHERE name = ?', (role,)).fetchone():
                    db.execute('INSERT INTO roles VALUES (?)', (role,))
                    db.executemany('INSERT INTO role_permissions VALUES (?, ?)', [(role, p) for p in permissions])
            if migrating:
                db.execute("UPDATE profiles SET role = 'ADMIN' WHERE rowid = (SELECT MIN(rowid) FROM profiles)")

    @staticmethod
    def public_user(db, row):
        return {key: row[key] for key in ('id', 'name', 'role', 'active')} | {
            'permissions': [r[0] for r in db.execute('SELECT permission FROM role_permissions WHERE role = ?', (row['role'],))]}

    def setup_required(self):
        with self.history.connect() as db:
            return not db.execute('SELECT 1 FROM profiles WHERE password_hash IS NOT NULL').fetchone()

    def setup(self, token, name, password):
        encoded = password_hash(password)
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not hmac.compare_digest(token, self.setup_token) or db.execute('SELECT 1 FROM profiles WHERE password_hash IS NOT NULL').fetchone():
                raise PermissionError('Setup is unavailable or the setup token is invalid')
            row = db.execute("SELECT * FROM profiles WHERE role = 'ADMIN' ORDER BY rowid LIMIT 1").fetchone()
            if row:
                user_id = row['id']
                db.execute('UPDATE profiles SET password_hash = ? WHERE id = ?', (encoded, user_id))
            else:
                user_id = str(uuid4())
                db.execute('INSERT INTO profiles (id,name,name_key,role,password_hash) VALUES (?,?,?,?,?)',
                           (user_id, name, name.casefold(), 'ADMIN', encoded))
            return self.public_user(db, db.execute('SELECT * FROM profiles WHERE id = ?', (user_id,)).fetchone())

    def login(self, name, password):
        with self.history.connect() as db:
            row = db.execute('SELECT * FROM profiles WHERE name_key = ?', (name.casefold(),)).fetchone()
            # Equal cost for unknown accounts.
            encoded = row['password_hash'] if row and row['password_hash'] else self.dummy_hash
            valid = password_matches(password, encoded)
            if not row or not row['active'] or not row['password_hash'] or not valid:
                raise PermissionError('Invalid account name or password')
            return self.public_user(db, row)

    def session(self, user_id):
        token = secrets.token_urlsafe(32)
        with self.history.connect() as db:
            db.execute('DELETE FROM sessions WHERE expires_at <= ?', (datetime.now(timezone.utc).isoformat(),))
            db.execute('INSERT INTO sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), user_id,
                       (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()))
        return token

    def current(self, token):
        with self.history.connect() as db:
            row = db.execute('''SELECT profiles.* FROM profiles JOIN sessions ON profiles.id = sessions.profile_id
                WHERE token_hash = ? AND expires_at > ? AND active = 1''',
                (hashlib.sha256(token.encode()).hexdigest(), datetime.now(timezone.utc).isoformat())).fetchone()
            return self.public_user(db, row) if row else None

    def logout(self, token):
        with self.history.connect() as db:
            db.execute('DELETE FROM sessions WHERE token_hash = ?', (hashlib.sha256(token.encode()).hexdigest(),))

    def users(self):
        with self.history.connect() as db:
            return [self.public_user(db, row) for row in db.execute('SELECT * FROM profiles ORDER BY name_key')]

    def create_user(self, name, password, role):
        encoded = password_hash(password)
        with self.history.connect() as db:
            role_row = db.execute('SELECT name FROM roles WHERE name = ?', (role,)).fetchone()
            if not role_row:
                raise ValueError('Unknown role')
            role = role_row['name']
            user_id = str(uuid4())
            db.execute('INSERT INTO profiles (id,name,name_key,role,password_hash) VALUES (?,?,?,?,?)',
                       (user_id, name, name.casefold(), role, encoded))
            return self.public_user(db, db.execute('SELECT * FROM profiles WHERE id = ?', (user_id,)).fetchone())

    @staticmethod
    def protect_admin(db):
        if not db.execute('''SELECT 1 FROM profiles JOIN role_permissions ON profiles.role = role_permissions.role
            WHERE active = 1 AND profiles.role = 'ADMIN' AND permission = 'roles:manage' ''').fetchone():
            raise ValueError('Keep at least one active ADMIN with roles:manage')

    def update_user(self, user_id, changes):
        if changes.get('password'):
            changes = dict(changes)
            changes['password_hash'] = password_hash(changes.pop('password'))
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM profiles WHERE id = ?', (user_id,)).fetchone():
                raise KeyError('User not found')
            if 'role' in changes:
                role_row = db.execute('SELECT name FROM roles WHERE name = ?', (changes['role'],)).fetchone()
                if not role_row:
                    raise ValueError('Unknown role')
                changes = dict(changes, role=role_row['name'])
            for key, value in changes.items():
                if key not in ('role', 'active', 'password_hash'):
                    raise ValueError('Unknown user field')
                db.execute(f'UPDATE profiles SET {key} = ? WHERE id = ?', (value, user_id))
            self.protect_admin(db)
            if 'password_hash' in changes or changes.get('active') is False:
                db.execute('DELETE FROM sessions WHERE profile_id = ?', (user_id,))
            return self.public_user(db, db.execute('SELECT * FROM profiles WHERE id = ?', (user_id,)).fetchone())

    def roles(self):
        with self.history.connect() as db:
            return [{'name': row['name'], 'permissions': [r[0] for r in db.execute(
                'SELECT permission FROM role_permissions WHERE role = ?', (row['name'],))]}
                for row in db.execute('SELECT name FROM roles ORDER BY name')]

    def save_role(self, name, permissions, create=False):
        if not set(permissions) <= set(PERMISSIONS):
            raise ValueError('Unknown permission')
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if create:
                db.execute('INSERT INTO roles VALUES (?)', (name,))
            else:
                role_row = db.execute('SELECT name FROM roles WHERE name = ?', (name,)).fetchone()
                if not role_row:
                    raise KeyError('Role not found')
                name = role_row['name']
            db.execute('DELETE FROM role_permissions WHERE role = ?', (name,))
            db.executemany('INSERT INTO role_permissions VALUES (?, ?)', [(name, p) for p in set(permissions)])
            self.protect_admin(db)

    def delete_role(self, name):
        if name.upper() in ('ADMIN', 'USER'):
            raise ValueError('Default roles cannot be deleted')
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM profiles WHERE role = ? COLLATE NOCASE', (name,)).fetchone():
                raise ValueError('Reassign users before deleting this role')
            if not db.execute('DELETE FROM roles WHERE name = ?', (name,)).rowcount:
                raise KeyError('Role not found')

    def saved_feedback(self, user_id, chat_id):
        with self.history.connect() as db:
            return {row['message_id']: dict(row) for row in db.execute(
                """SELECT response_feedback.* FROM response_feedback JOIN turns
                ON turns.id = response_feedback.message_id WHERE turns.chat_id = ? AND user_id = ?""",
                (chat_id, user_id))}

    def feedback(self, user, message_id, rating, comment):
        with self.history.connect() as db:
            row = db.execute('''SELECT turns.payload FROM turns JOIN chats ON chats.id = turns.chat_id
                WHERE turns.id = ? AND chats.profile_id = ?''', (message_id, user['id'])).fetchone()
            if not row:
                raise KeyError('Response not found')
            if json.loads(row['payload']).get('status') != 'complete':
                raise ValueError('Feedback is available only for completed responses')
            feedback = dict(id=str(uuid4()), message_id=message_id, user_id=user['id'], role=user['role'],
                            rating=rating, comment=comment, timestamp=datetime.now(timezone.utc).isoformat())
            db.execute('''INSERT INTO response_feedback VALUES (:id,:message_id,:user_id,:role,:rating,:comment,:timestamp)
                ON CONFLICT(message_id,user_id) DO UPDATE SET role=excluded.role, rating=excluded.rating,
                comment=excluded.comment, timestamp=excluded.timestamp''', feedback)
            return dict(db.execute('SELECT * FROM response_feedback WHERE message_id = ? AND user_id = ?',
                                   (message_id, user['id'])).fetchone())
