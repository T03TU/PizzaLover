"""Explicitly reset local accounts and seed one ADMIN; never runs at startup."""
import argparse
import json
import secrets
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from src.access import Access, PERMISSIONS, USER_PERMISSIONS, password_hash
from src.chat_history import ChatHistory
from src.contacts import normalize_email, normalize_phone
from src.recovery import Recovery


def reset_users(path, email='admin@example.com', phone='+12025550123', country='US'):
    email = normalize_email(email)
    phone = normalize_phone(phone, country)
    history = ChatHistory(path)
    Access(history)
    Recovery(history)
    password = secrets.token_urlsafe(18)
    encoded = password_hash(password)
    with history.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT count(*) FROM profiles').fetchone()[0]
        for table in ('password_resets', 'auth_rate_limits', 'sessions', 'response_feedback', 'turns', 'chats', 'profiles', 'role_permissions', 'roles'):
            db.execute(f'DELETE FROM {table}')
        for name, permissions in [('ADMIN', PERMISSIONS), ('USER', USER_PERMISSIONS)]:
            db.execute('INSERT INTO roles VALUES (?)', (name,))
            db.executemany('INSERT INTO role_permissions VALUES (?,?)', [(name, permission) for permission in permissions])
        db.execute('''INSERT INTO profiles (id,name,name_key,role,password_hash,email,phone,active)
            VALUES (?,?,?,?,?,?,?,1)''', (str(uuid4()), 'ADMIN', 'admin', 'ADMIN', encoded, email, phone))
    access = Access(history)
    assert access.login('ADMIN', password)['role'] == 'ADMIN'
    assert access.login(email, password)['role'] == 'ADMIN'
    assert access.login(phone, password)['role'] == 'ADMIN'
    return {'removed_users': previous, 'username': 'ADMIN', 'password': password, 'email': email, 'phone': phone}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Delete all accounts, sessions, reset tokens, chats and feedback, and seed ADMIN. Embedded and staged reviews remain.')
    parser.add_argument('--confirm', choices=['RESET-USERS'], required=True)
    parser.add_argument('--email', default='admin@example.com')
    parser.add_argument('--phone', default='+12025550123')
    parser.add_argument('--country', default='US')
    args = parser.parse_args()
    print(json.dumps(reset_users(config.CHAT_HISTORY_PATH, args.email, args.phone, args.country), indent=2))
