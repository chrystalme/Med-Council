"""Alembic env — reads DATABASE_URL from the environment.

We don't use SQLAlchemy models as the source-of-truth (the app code writes SQL
directly via psycopg). Migrations are therefore written by hand as raw SQL —
`autogenerate` is not meaningful here and is disabled.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# Load .env so DATABASE_URL is picked up when running `alembic` from the shell.
try:
    from dotenv import load_dotenv  # type: ignore

    from pathlib import Path as _Path

    load_dotenv(_Path(__file__).resolve().parent.parent / ".env", override=False)
    load_dotenv(override=False)
except Exception:
    pass


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_db_url = os.environ.get("DATABASE_URL", "").strip()
if not _db_url:
    raise RuntimeError(
        "DATABASE_URL must be set to run migrations. "
        "Example: postgresql://$USER@localhost:5432/medai_council"
    )
# SQLAlchemy needs the `postgresql+psycopg` driver prefix to pick psycopg3.
if _db_url.startswith("postgres://"):
    _db_url = "postgresql://" + _db_url[len("postgres://") :]
if _db_url.startswith("postgresql://"):
    _db_url = "postgresql+psycopg://" + _db_url[len("postgresql://") :]

# NOTE: we deliberately do NOT call `config.set_main_option("sqlalchemy.url", _db_url)`
# and we do NOT use `engine_from_config`. ConfigParser treats `%` as interpolation
# syntax, which breaks URL-encoded passwords (`%5D`, `%3A`, …). `create_engine`
# accepts the raw URL string and bypasses the parser entirely.


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_db_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
