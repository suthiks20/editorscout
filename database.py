import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect(database_path):
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(database_path, schema_path=None):
    schema_file = Path(schema_path) if schema_path else Path(__file__).with_name("schema.sql")
    with connect(database_path) as connection:
        connection.executescript(schema_file.read_text(encoding="utf-8"))
