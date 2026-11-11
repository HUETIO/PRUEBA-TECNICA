from __future__ import annotations

import os

import psycopg2
from psycopg2 import sql

from src.common import ROOT, load_env, pg_config


ROLE_ENV = {
    "rol_ingesta": "PG_INGEST_PASSWORD",
    "rol_analista": "PG_ANALYST_PASSWORD",
    "rol_auditor": "PG_AUDITOR_PASSWORD",
}


def initialize_database() -> None:
    load_env()
    connection = psycopg2.connect(**pg_config("super"))
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            for role, password_key in ROLE_ENV.items():
                password = os.getenv(password_key)
                if not password:
                    raise RuntimeError(f"{password_key} is required")
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
                exists = cursor.fetchone() is not None
                statement = "ALTER ROLE {} WITH LOGIN PASSWORD {}" if exists else "CREATE ROLE {} WITH LOGIN PASSWORD {}"
                cursor.execute(
                    sql.SQL(statement).format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )
            schema = (ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
            cursor.execute(schema)
    finally:
        connection.close()


def main() -> None:
    initialize_database()
    print("PostgreSQL schema and restricted roles are ready.")


if __name__ == "__main__":
    main()
