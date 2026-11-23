"""Isolated pre-embedding staging with SQLite's VM authorizer as the SQL boundary."""
import math
import sqlite3
from contextlib import contextmanager

from api.schemas import Review

FIELDS = tuple(Review.model_fields)


class Pipeline:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS staging_reviews (
                Feedback_ID TEXT PRIMARY KEY NOT NULL, Franchise TEXT NOT NULL,
                Location TEXT NOT NULL, Rating INTEGER NOT NULL, Comment TEXT NOT NULL)''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.enable_load_extension(False)
        try:
            with db:
                yield db
        finally:
            db.close()

    def stage(self, reviews):
        with self.connect() as db:
            db.executemany('INSERT INTO staging_reviews VALUES (?,?,?,?,?)',
                           [tuple(r.model_dump()[field] for field in FIELDS) for r in reviews])
        return len(reviews)

    def rows(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM staging_reviews ORDER BY Feedback_ID')]

    def clear(self):
        with self.connect() as db:
            db.execute('DELETE FROM staging_reviews')

    def query(self, sql):
        # execute() accepts one statement only; all capabilities are denied unless
        # explicitly allowed here, including schema/pragma/attach and extension loading.
        functions = {'abs', 'avg', 'cast', 'char', 'coalesce', 'count', 'format', 'glob',
                     'hex', 'ifnull', 'iif', 'instr', 'length', 'like', 'likelihood',
                     'lower', 'ltrim', 'max', 'min', 'nullif', 'replace', 'round', 'rtrim',
                     'substr', 'substring', 'sum', 'total', 'trim', 'typeof', 'unicode', 'upper'}
        def authorize(action, arg1, arg2, database, source):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action in (sqlite3.SQLITE_READ, sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                return sqlite3.SQLITE_OK if database in ('main', None) and arg1 == 'staging_reviews' else sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION:
                return sqlite3.SQLITE_OK if (arg2 or '').lower() in functions else sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_DENY
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            before = db.total_changes
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 100000)
            db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 20000)
            db.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 50)
            db.set_authorizer(authorize)
            steps = 0
            def budget():
                nonlocal steps
                steps += 1
                return steps > 1000
            db.set_progress_handler(budget, 1000)
            try:
                cursor = db.execute(sql)
                columns = [column[0] for column in cursor.description or []]
                # Drain bounded result sets before commit, including UPDATE RETURNING.
                rows = cursor.fetchmany(501) if columns else []
                if len(rows) > 500:
                    cursor.close()
                else:
                    cursor.fetchall()
                def json_value(value):
                    if isinstance(value, bytes):
                        return value.hex()
                    if isinstance(value, float) and not math.isfinite(value):
                        return str(value)
                    return value
                return {'columns': columns, 'rows': [[json_value(value) for value in row] for row in rows[:500]],
                        'row_count': len(rows[:500]), 'affected_rows': db.total_changes - before,
                        'truncated': len(rows) > 500}
            finally:
                db.set_authorizer(None)
                db.set_progress_handler(None, 0)
