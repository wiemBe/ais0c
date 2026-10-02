#!/bin/sh
# Creates the roles and databases of the dev stack. The postgres image runs this once, on the
# first start with an empty data directory (/docker-entrypoint-initdb.d).
#
# - ais0c: application role, owner of the ais0c database (docs/impl/data-model.md).
# - temporal: Temporal's role, owner of the temporal and temporal_visibility databases. Temporal
#   keeps its visibility store in a database of its own.
#
# Neither role is a superuser and neither can connect to the other's databases. pgvector is
# not a trusted extension, so a non-superuser cannot create it; it is created here, as the
# superuser, in the ais0c database.
set -e

: "${AIS0C_DB_PASSWORD:?AIS0C_DB_PASSWORD is required}"
: "${TEMPORAL_DB_PASSWORD:?TEMPORAL_DB_PASSWORD is required}"

# psql quotes :'name' variables itself, so passwords never pass through the shell or the SQL text.
psql --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname=postgres \
    --set=ais0c_password="$AIS0C_DB_PASSWORD" \
    --set=temporal_password="$TEMPORAL_DB_PASSWORD" <<'SQL'
CREATE ROLE ais0c LOGIN PASSWORD :'ais0c_password';
CREATE ROLE temporal LOGIN PASSWORD :'temporal_password';

CREATE DATABASE ais0c OWNER ais0c;
CREATE DATABASE temporal OWNER temporal;
CREATE DATABASE temporal_visibility OWNER temporal;

-- New databases grant CONNECT to PUBLIC; only the owner (and the superuser) may connect.
REVOKE ALL ON DATABASE ais0c FROM PUBLIC;
REVOKE ALL ON DATABASE temporal FROM PUBLIC;
REVOKE ALL ON DATABASE temporal_visibility FROM PUBLIC;

\connect ais0c
CREATE EXTENSION vector;
SQL
