#!/bin/sh
set -eu
umask 077
# PostgreSQL initialization runs as postgres. Keep host secrets private while
# granting only that user a private, ephemeral copy of the application password.
mkdir -p /run/tauric-db
cp /run/secrets/database-app /run/tauric-db/database-app
chown -R postgres:postgres /run/tauric-db
export TAURIC_DB_PASSWORD_FILE=/run/tauric-db/database-app
exec /usr/local/bin/docker-entrypoint.sh "$@"
