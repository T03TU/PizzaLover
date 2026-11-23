"""Persistent permissions, password credentials and opaque browser sessions."""
import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from src.contacts import normalize_email, normalize_phone

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
            session_columns = {row['name'] for row in db.execute('PRAGMA table_info(sessions)')}
            if 'active_role' not in session_columns:
                db.execute('ALTER TABLE sessions ADD COLUMN active_role TEXT')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(profiles)')}
            migrating = 'role' not in columns
            if migrating:
                db.execute("ALTER TABLE profiles ADD COLUMN role TEXT NOT NULL DEFAULT 'USER'")
                db.execute('ALTER TABLE profiles ADD COLUMN password_hash TEXT')
                db.execute('ALTER TABLE profiles ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
            for column in ('email', 'phone'):
                if column not in columns:
                    db.execute(f'ALTER TABLE profiles ADD COLUMN {column} TEXT')
                db.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS profiles_{column} ON profiles({column}) WHERE {column} IS NOT NULL')
            for role, permissions in [('ADMIN', PERMISSIONS), ('USER', USER_PERMISSIONS)]:
                if not db.execute('SELECT 1 FROM roles WHERE name = ?', (role,)).fetchone():
                    db.execute('INSERT INTO roles VALUES (?)', (role,))
                    db.executemany('INSERT INTO role_permissions VALUES (?, ?)', [(role, p) for p in permissions])
            if migrating:
                db.execute("UPDATE profiles SET role = 'ADMIN' WHERE rowid = (SELECT MIN(rowid) FROM profiles)")
            if 'username' not in columns:
                db.execute('ALTER TABLE profiles ADD COLUMN username TEXT COLLATE NOCASE')
            for row in db.execute('SELECT id,name FROM profiles WHERE username IS NULL ORDER BY rowid').fetchall():
                db.execute('UPDATE profiles SET username=? WHERE id=?', (self.available_username(db, row['name']), row['id']))
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS profiles_username ON profiles(username COLLATE NOCASE)')
            db.execute("""CREATE TABLE IF NOT EXISTS profile_roles (
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                role TEXT NOT NULL REFERENCES roles(name), PRIMARY KEY(profile_id,role))""")
            db.execute("""INSERT INTO profile_roles SELECT id,role FROM profiles
                WHERE NOT EXISTS (SELECT 1 FROM profile_roles WHERE profile_id=profiles.id)""")
            feedback_columns = {row['name'] for row in db.execute('PRAGMA table_info(response_feedback)')}
            if 'roles' not in feedback_columns:
                db.execute('ALTER TABLE response_feedback ADD COLUMN roles TEXT')
                for row in db.execute('SELECT id,role FROM response_feedback').fetchall():
                    db.execute('UPDATE response_feedback SET roles=? WHERE id=?', (json.dumps([row['role']]),row['id']))


    @staticmethod
    def membership(db, user_id):
        return [r[0] for r in db.execute('SELECT role FROM profile_roles WHERE profile_id=? ORDER BY role', (user_id,))]

    @staticmethod
    def available_username(db, name):
        base = re.sub(r'[^A-Za-z0-9]', '', name)[:70] or 'User'
        candidate = base
        counter = 1
        while db.execute('SELECT 1 FROM profiles WHERE username=?', (candidate,)).fetchone():
            counter += 1
            candidate = base + str(counter)
        return candidate

    @staticmethod
    def validate_username(username):
        if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9]{1,80}', username):
            raise ValueError('Username must contain 1–80 letters or numbers only')
        return username

    @staticmethod
    def assign_roles(db, user_id, roles):
        if not roles or len(roles) > 50:
            raise ValueError('Assign between 1 and 50 roles')
        canonical = []
        for name in roles:
            row = db.execute('SELECT name FROM roles WHERE name=?', (name,)).fetchone()
            if not row:
                raise ValueError('Unknown role: ' + name)
            if row['name'] in canonical:
                raise ValueError('Roles must be unique')
            canonical.append(row['name'])
        db.execute('DELETE FROM profile_roles WHERE profile_id=?', (user_id,))
        db.executemany('INSERT INTO profile_roles VALUES (?,?)', [(user_id, role) for role in canonical])
        # Keep the original field for older API clients; membership drives authorization.
        db.execute('UPDATE profiles SET role=? WHERE id=?', ('ADMIN' if 'ADMIN' in canonical else canonical[0], user_id))

    @staticmethod
    def public_user(db, row):
        roles = Access.membership(db, row['id'])
        permissions = [r[0] for r in db.execute("""SELECT DISTINCT permission FROM role_permissions
            JOIN profile_roles ON profile_roles.role=role_permissions.role
            WHERE profile_id=? ORDER BY permission""", (row['id'],))]
        return {key: row[key] for key in ('id', 'name', 'username', 'role', 'active', 'email', 'phone')} | {
            'roles': roles, 'permissions': permissions}

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
            if not self.membership(db, user_id):
                self.assign_roles(db, user_id, ['ADMIN'])
            row = db.execute('SELECT * FROM profiles WHERE id=?', (user_id,)).fetchone()
            if not row['username']:
                db.execute('UPDATE profiles SET username=? WHERE id=?', (self.available_username(db, row['name']), user_id))
            return self.public_user(db, db.execute('SELECT * FROM profiles WHERE id = ?', (user_id,)).fetchone())

    def login(self, name, password, country='NZ', identifier_type=None):
        name = name.strip()
        email = None
        phone = None
        try:
            if '@' in name:
                email = normalize_email(name)
            elif name.startswith('+') or any(char.isdigit() for char in name):
                phone = normalize_phone(name, country)
        except ValueError:
            pass
        with self.history.connect() as db:
            if identifier_type == 'phone':
                row = db.execute('SELECT * FROM profiles WHERE phone=?', (phone,)).fetchone()
            else:
                row = db.execute('SELECT * FROM profiles WHERE username=?', (name,)).fetchone()
                if not row:
                    row = db.execute('SELECT * FROM profiles WHERE email=?', (email,)).fetchone()
                if not row and identifier_type is None:
                    row = db.execute('SELECT * FROM profiles WHERE phone=?', (phone,)).fetchone()
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
            row = db.execute('SELECT role FROM profiles WHERE id=?', (user_id,)).fetchone()
            db.execute('INSERT INTO sessions (token_hash,profile_id,expires_at,active_role) VALUES (?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), user_id,
                       (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(), row['role']))
        return token

    def current(self, token):
        with self.history.connect() as db:
            row = db.execute("""SELECT profiles.*,sessions.active_role FROM profiles JOIN sessions ON profiles.id = sessions.profile_id
                WHERE token_hash = ? AND expires_at > ? AND active = 1""",
                (hashlib.sha256(token.encode()).hexdigest(), datetime.now(timezone.utc).isoformat())).fetchone()
            if not row:
                return None
            user = self.public_user(db, row)
            selected = row['active_role']
            if selected not in user['roles']:
                selected = user['role'] if user['role'] in user['roles'] else user['roles'][0]
                db.execute('UPDATE sessions SET active_role=? WHERE token_hash=?', (selected, hashlib.sha256(token.encode()).hexdigest()))
            user['active_role'] = selected
            user['role'] = selected
            user['permissions'] = [r[0] for r in db.execute('SELECT permission FROM role_permissions WHERE role=? ORDER BY permission', (selected,))]
            return user

    def switch_role(self, token, role):
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            session = db.execute("""SELECT sessions.profile_id FROM sessions JOIN profiles ON profiles.id=profile_id
                WHERE token_hash=? AND expires_at>? AND active=1""", (hashlib.sha256(token.encode()).hexdigest(), datetime.now(timezone.utc).isoformat())).fetchone()
            if not session:
                raise PermissionError('Sign in first')
            assigned = db.execute('SELECT role FROM profile_roles WHERE profile_id=? AND role=? COLLATE NOCASE', (session['profile_id'],role)).fetchone()
            if not assigned:
                raise PermissionError('This role is not assigned to your account')
            db.execute('UPDATE sessions SET active_role=? WHERE token_hash=?', (assigned['role'],hashlib.sha256(token.encode()).hexdigest()))
        return self.current(token)

    def logout(self, token):
        with self.history.connect() as db:
            db.execute('DELETE FROM sessions WHERE token_hash = ?', (hashlib.sha256(token.encode()).hexdigest(),))

    def users(self):
        with self.history.connect() as db:
            return [self.public_user(db, row) for row in db.execute('SELECT * FROM profiles ORDER BY name_key')]

    def create_user(self, name, password, role='USER', email=None, phone=None, country='NZ', roles=None, username=None):
        email = normalize_email(email)
        phone = normalize_phone(phone, country)
        encoded = password_hash(password)
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            username = self.validate_username(username) if username is not None else self.available_username(db, name)
            user_id = str(uuid4())
            db.execute('INSERT INTO profiles (id,name,name_key,role,password_hash,email,phone,username) VALUES (?,?,?,?,?,?,?,?)',
                       (user_id, name, name.casefold(), 'USER', encoded, email, phone, username))
            self.assign_roles(db, user_id, roles if roles is not None else [role])
            return self.public_user(db, db.execute('SELECT * FROM profiles WHERE id = ?', (user_id,)).fetchone())

    @staticmethod
    def protect_admin(db):
        if not db.execute("""SELECT 1 FROM profiles WHERE active=1
            AND EXISTS (SELECT 1 FROM profile_roles WHERE profile_id=profiles.id AND role='ADMIN')
            AND EXISTS (SELECT 1 FROM profile_roles JOIN role_permissions ON profile_roles.role=role_permissions.role
                WHERE profile_id=profiles.id AND permission='roles:manage')""").fetchone():
            raise ValueError('Keep at least one active ADMIN with roles:manage')

    def update_user(self, user_id, changes):
        changes = dict(changes)
        country = changes.pop('country', 'NZ')
        if 'email' in changes:
            changes['email'] = normalize_email(changes['email'])
        if 'phone' in changes:
            changes['phone'] = normalize_phone(changes['phone'], country)
        if changes.get('password'):
            changes = dict(changes)
            changes['password_hash'] = password_hash(changes.pop('password'))
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM profiles WHERE id = ?', (user_id,)).fetchone():
                raise KeyError('User not found')
            if 'role' in changes and 'roles' in changes:
                raise ValueError('Supply roles or the legacy role field, not both')
            if 'roles' in changes:
                self.assign_roles(db, user_id, changes.pop('roles'))
            elif 'role' in changes:
                self.assign_roles(db, user_id, [changes.pop('role')])
            if 'username' in changes:
                changes['username'] = self.validate_username(changes['username'])
            for key, value in changes.items():
                if key not in ('active', 'password_hash', 'email', 'phone', 'username'):
                    raise ValueError('Unknown user field')
                db.execute(f'UPDATE profiles SET {key} = ? WHERE id = ?', (value, user_id))
            self.protect_admin(db)
            if 'password_hash' in changes or changes.get('active') is False:
                db.execute('DELETE FROM sessions WHERE profile_id = ?', (user_id,))
            if 'password_hash' in changes or 'email' in changes or 'phone' in changes or changes.get('active') is False:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='password_resets'").fetchone():
                    db.execute('DELETE FROM password_resets WHERE profile_id=?', (user_id,))
            return self.public_user(db, db.execute('SELECT * FROM profiles WHERE id = ?', (user_id,)).fetchone())

    def remove_user(self, actor_id, user_id):
        """Remove only strictly lower-privilege accounts; retain feedback audit records."""
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT * FROM profiles WHERE id = ? AND active = 1', (actor_id,)).fetchone()
            target = db.execute('SELECT * FROM profiles WHERE id = ?', (user_id,)).fetchone()
            if not actor or 'ADMIN' not in self.membership(db, actor_id):
                raise PermissionError('Only ADMIN can remove users')
            if not target:
                raise KeyError('User not found')
            actor_permissions = set(self.public_user(db, actor)['permissions'])
            target_permissions = set(self.public_user(db, target)['permissions'])
            if user_id == actor_id or 'ADMIN' in self.membership(db, user_id) or not target_permissions < actor_permissions:
                raise PermissionError('Only users with strictly lower privileges can be removed')
            db.execute('DELETE FROM sessions WHERE profile_id = ?', (user_id,))
            db.execute('DELETE FROM chats WHERE profile_id = ?', (user_id,))
            db.execute('DELETE FROM profiles WHERE id = ?', (user_id,))
            self.protect_admin(db)

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
            if db.execute('SELECT 1 FROM profile_roles WHERE role = ? COLLATE NOCASE', (name,)).fetchone():
                raise ValueError('Reassign users before deleting this role')
            if not db.execute('DELETE FROM roles WHERE name = ?', (name,)).rowcount:
                raise KeyError('Role not found')

    def saved_feedback(self, user_id, chat_id):
        with self.history.connect() as db:
            return {row['message_id']: self.public_feedback(row) for row in db.execute(
                """SELECT response_feedback.* FROM response_feedback JOIN turns
                ON turns.id = response_feedback.message_id WHERE turns.chat_id = ? AND user_id = ?""",
                (chat_id, user_id))}

    @staticmethod
    def public_feedback(row):
        result = dict(row)
        result['roles'] = json.loads(result['roles']) if result.get('roles') else [result['role']]
        return result

    def feedback(self, user, message_id, rating, comment):
        with self.history.connect() as db:
            row = db.execute('''SELECT turns.payload FROM turns JOIN chats ON chats.id = turns.chat_id
                WHERE turns.id = ? AND chats.profile_id = ?''', (message_id, user['id'])).fetchone()
            if not row:
                raise KeyError('Response not found')
            if json.loads(row['payload']).get('status') != 'complete':
                raise ValueError('Feedback is available only for completed responses')
            feedback = dict(id=str(uuid4()), message_id=message_id, user_id=user['id'], role=user['role'],
                            rating=rating, comment=comment, timestamp=datetime.now(timezone.utc).isoformat(), roles=json.dumps(user['roles']))
            db.execute('''INSERT INTO response_feedback (id,message_id,user_id,role,rating,comment,timestamp,roles) VALUES (:id,:message_id,:user_id,:role,:rating,:comment,:timestamp,:roles)
                ON CONFLICT(message_id,user_id) DO UPDATE SET role=excluded.role, rating=excluded.rating,
                comment=excluded.comment, timestamp=excluded.timestamp, roles=excluded.roles''', feedback)
            return self.public_feedback(db.execute('SELECT * FROM response_feedback WHERE message_id = ? AND user_id = ?',
                                   (message_id, user['id'])).fetchone())
