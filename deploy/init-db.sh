#!/bin/sh
set -eu
if [ -n "${TAURIC_DB_PASSWORD_FILE:-}" ]; then
    TAURIC_DB_PASSWORD=$(cat "$TAURIC_DB_PASSWORD_FILE")
fi
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=ON_ERROR_STOP=1 --set=app_password="$TAURIC_DB_PASSWORD" <<'SQL'
CREATE ROLE tauric_app LOGIN PASSWORD :'app_password';
REVOKE ALL ON DATABASE tauric FROM PUBLIC;
GRANT CONNECT ON DATABASE tauric TO tauric_app;
SQL
