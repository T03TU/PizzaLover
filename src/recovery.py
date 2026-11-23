"""Persistent, rate-limited password recovery with single-use codes and grants."""
import hashlib
import hmac
import logging
import os
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from uuid import uuid4

import httpx

from src.access import password_hash
from src.contacts import normalize_email, normalize_phone

logger = logging.getLogger(__name__)
GENERIC_MESSAGE = 'If an account matches those details, a verification code has been sent.'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def delivery_modes():
    return {'email': 'smtp' if os.getenv('SMTP_HOST') else 'console',
            'phone': 'sms' if os.getenv('SMS_WEBHOOK_URL') else 'console'}


def deliver_code(channel, destination, code):
    message = f'Your PizzaLover password reset code is {code}. It expires in 10 minutes.'
    mode = delivery_modes()[channel]
    if mode == 'console':
        logger.warning('PASSWORD RESET (development console): %s %s — code %s — expires in 10 minutes', channel, destination, code)
    elif mode == 'smtp':
        mail = EmailMessage()
        mail['From'] = os.environ['SMTP_FROM']
        mail['To'] = destination
        mail['Subject'] = 'PizzaLover password reset'
        mail.set_content(message)
        tls = os.getenv('SMTP_TLS', 'starttls')
        client_class = smtplib.SMTP_SSL if tls == 'ssl' else smtplib.SMTP
        with client_class(os.environ['SMTP_HOST'], int(os.getenv('SMTP_PORT', '465' if tls == 'ssl' else '587')), timeout=10) as client:
            if tls == 'starttls':
                client.starttls(context=ssl.create_default_context())
            if os.getenv('SMTP_USERNAME'):
                client.login(os.environ['SMTP_USERNAME'], os.environ['SMTP_PASSWORD'])
            client.send_message(mail)
    else:
        headers = {}
        if os.getenv('SMS_WEBHOOK_TOKEN'):
            headers['Authorization'] = 'Bearer ' + os.environ['SMS_WEBHOOK_TOKEN']
        with httpx.Client(timeout=10) as client:
            client.post(os.environ['SMS_WEBHOOK_URL'], headers=headers,
                        json={'to': destination, 'message': message}).raise_for_status()


class RateLimited(ValueError):
    pass


class Recovery:
    def __init__(self, history):
        self.history = history
        with history.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS password_resets (
                    id TEXT PRIMARY KEY, profile_id TEXT REFERENCES profiles(id) ON DELETE CASCADE,
                    identifier_hash TEXT NOT NULL, code_hash TEXT NOT NULL, expires_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, verified INTEGER NOT NULL DEFAULT 0,
                    grant_hash TEXT UNIQUE);
                CREATE INDEX IF NOT EXISTS resets_by_identifier ON password_resets(identifier_hash);
                CREATE TABLE IF NOT EXISTS auth_rate_limits (
                    bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at TEXT NOT NULL);
            ''')

    @staticmethod
    def limit(db, key, maximum, minutes=10):
        now = datetime.now(timezone.utc)
        db.execute('DELETE FROM auth_rate_limits WHERE expires_at <= ?', (now.isoformat(),))
        bucket = digest(key)
        row = db.execute('SELECT count FROM auth_rate_limits WHERE bucket=?', (bucket,)).fetchone()
        if row and row['count'] >= maximum:
            return False
        db.execute('''INSERT INTO auth_rate_limits VALUES (?,1,?) ON CONFLICT(bucket)
            DO UPDATE SET count=count+1''', (bucket, (now + timedelta(minutes=minutes)).isoformat()))
        return True

    def login_limit(self, address):
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            allowed = self.limit(db, 'login:' + address, 30)
        if not allowed:
            raise RateLimited('Too many sign-in attempts. Try again in 10 minutes.')

    def request(self, channel, identifier, country, address):
        destination = normalize_email(identifier) if channel == 'email' else normalize_phone(identifier, country)
        if not destination:
            raise ValueError('Enter an email address or phone number')
        key = digest(channel + ':' + destination)
        request_id = str(uuid4())
        code = f'{secrets.randbelow(1000000):06d}'
        now = datetime.now(timezone.utc)
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            ip_allowed = self.limit(db, 'request-ip:' + address, 15)
            contact_allowed = self.limit(db, 'request-contact:' + key, 5)
            allowed = ip_allowed and contact_allowed
            if allowed:
                db.execute('DELETE FROM password_resets WHERE expires_at <= ?', (now.isoformat(),))
                column = 'email' if channel == 'email' else 'phone'
                user = db.execute(f'SELECT id FROM profiles WHERE {column}=? AND active=1', (destination,)).fetchone()
                # A new request invalidates codes and verified grants across both channels.
                db.execute('DELETE FROM password_resets WHERE identifier_hash=?', (key,))
                if user:
                    db.execute('DELETE FROM password_resets WHERE profile_id=?', (user['id'],))
                db.execute('''INSERT INTO password_resets (id,profile_id,identifier_hash,code_hash,expires_at)
                    VALUES (?,?,?,?,?)''', (request_id, user['id'] if user else None, key,
                    digest(request_id + ':' + code), (now + timedelta(minutes=10)).isoformat()))
        if not allowed:
            raise RateLimited('Too many reset requests. Try again in 10 minutes.')
        if user:
            try:
                deliver_code(channel, destination, code)
            except Exception:
                # Never return different responses based on account existence or delivery.
                logger.error('Password reset delivery failed; check the configured provider')
        return {'message': GENERIC_MESSAGE, 'request_id': request_id}

    def verify(self, request_id, code, address):
        grant = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            allowed = self.limit(db, 'verify-ip:' + address, 30)
            row = db.execute('''SELECT password_resets.* FROM password_resets JOIN profiles
                ON profiles.id=password_resets.profile_id WHERE password_resets.id=? AND active=1''', (request_id,)).fetchone()
            valid = bool(allowed and row and row['expires_at'] > now.isoformat() and row['attempts'] < 5
                         and not row['verified'] and hmac.compare_digest(row['code_hash'], digest(request_id + ':' + code)))
            if row and not row['verified']:
                db.execute('UPDATE password_resets SET attempts=attempts+1 WHERE id=?', (request_id,))
            if valid:
                db.execute('UPDATE password_resets SET verified=1, grant_hash=? WHERE id=?', (digest(grant), request_id))
        if not allowed:
            raise RateLimited('Too many verification attempts. Try again in 10 minutes.')
        if not valid:
            raise ValueError('Invalid or expired code. Request a new code if needed.')
        return {'reset_token': grant}

    def complete(self, token, password, address='local'):
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            allowed = self.limit(db, 'complete-ip:' + address, 15)
        if not allowed:
            raise RateLimited('Too many reset attempts. Try again in 10 minutes.')
        encoded = password_hash(password)
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT password_resets.* FROM password_resets JOIN profiles
                ON profiles.id=password_resets.profile_id WHERE grant_hash=? AND verified=1 AND active=1
                AND expires_at > ?''', (digest(token), datetime.now(timezone.utc).isoformat())).fetchone()
            if not row:
                raise ValueError('Invalid or expired reset token. Request a new code.')
            db.execute('UPDATE profiles SET password_hash=? WHERE id=?', (encoded, row['profile_id']))
            db.execute('DELETE FROM sessions WHERE profile_id=?', (row['profile_id'],))
            db.execute('DELETE FROM password_resets WHERE profile_id=?', (row['profile_id'],))
        return {'message': 'Password updated. Sign in with your new password.'}
