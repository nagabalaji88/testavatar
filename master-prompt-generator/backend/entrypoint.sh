#!/bin/sh
# Apply outstanding migrations, then hand off to the real command.
#
# Only one process may do this. The worker and the API start from the same
# image, and two concurrent `alembic upgrade head` runs against one database
# race on the version table -- so this is opt-in per service via
# RUN_MIGRATIONS, set on the API in compose and left unset on the worker.
#
# Set RUN_MIGRATIONS=false anywhere the schema is applied out of band: a
# Kubernetes init container, a release job, or a DBA running it by hand. The
# application refuses to create tables itself outside local (see
# app/db/session.py), so skipping both means starting against no schema at
# all -- which fails loudly on the first query rather than corrupting anything.
set -e

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "entrypoint: applying migrations"
    alembic upgrade head
    echo "entrypoint: schema at $(alembic current 2>/dev/null | tail -1)"
else
    echo "entrypoint: RUN_MIGRATIONS is not true, leaving the schema alone"
fi

exec "$@"
