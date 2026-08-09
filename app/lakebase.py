"""Lakebase (managed Postgres) connection helper.

Single source of truth for how the notebook (Step 2) and the app/agent
(Steps 4-5) reach Lakebase. Lives in ``app/`` because Databricks Apps deploys
*only* that folder — anything at the repo root wouldn't ship.

The full Postgres DSN is stored as one Databricks secret and read at call time;
nothing here hardcodes a credential.

    scope: lakebase-recalls
    key:   lakebase-recalls-url
    value: postgresql://agent:<password>@<host>/databricks_postgres?sslmode=require

Usage:

    from lakebase import connect          # app/notebook, same folder or sys.path
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM recalls")
            print(cur.fetchone()[0])

One connection identity for the MVP: the DSN's ``agent`` role is a superuser,
so both the notebook load and the app's reads/writes use it. Splitting
identities is hardening, not MVP.
"""

import os

import psycopg2

SECRET_SCOPE = "lakebase-recalls"
SECRET_KEY = "lakebase-recalls-url"

# Optional override: if the DSN is injected directly as an env var (e.g. a
# Databricks App resource binding), use it and skip the secrets API entirely.
# This still reads from the environment — it never hardcodes a credential.
ENV_VAR = "LAKEBASE_URL"


def _notebook_dbutils():
    """The ``dbutils`` a notebook injects into ``__main__`` — not visible to an
    imported module's own globals, so we fetch it explicitly. Returns None
    outside a notebook."""
    try:
        import __main__

        return getattr(__main__, "dbutils", None)
    except Exception:
        return None


def _sdk_dbutils():
    """``dbutils`` via the Databricks SDK — works in notebook and non-notebook
    (app / agent) contexts using ambient workspace auth."""
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient().dbutils


def get_dsn(dbutils=None):
    """Resolve the Lakebase DSN. Resolution order, first hit wins:

    1. ``LAKEBASE_URL`` env var (explicit override / app env injection).
    2. A ``dbutils`` passed in by the caller.
    3. The notebook-global ``dbutils`` (Step 2 notebook).
    4. ``dbutils`` from the Databricks SDK (app / agent).
    """
    env_dsn = os.environ.get(ENV_VAR)
    if env_dsn:
        return env_dsn

    du = dbutils or _notebook_dbutils() or _sdk_dbutils()
    return du.secrets.get(scope=SECRET_SCOPE, key=SECRET_KEY)


def connect(dbutils=None):
    """Open a psycopg2 connection to Lakebase. Caller owns closing it
    (``with connect() as conn:`` commits/rolls back on exit but does not close;
    close explicitly or let the process end)."""
    return psycopg2.connect(get_dsn(dbutils))
