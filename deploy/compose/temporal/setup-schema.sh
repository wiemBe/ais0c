#!/bin/sh
# Creates or upgrades Temporal's tables (compose service temporal-schema).
#
# The temporal role and its databases come from postgres/init/10-databases.sh, so this script
# only manages tables. Based on Temporal's official compose setup (temporalio/samples-server,
# compose/scripts/setup-postgres.sh). It is safe to run on every start: setup-schema skips a
# database that already has a schema version and update-schema applies only missing versions.
#
# Connection settings come from SQL_PLUGIN, SQL_HOST, SQL_PORT, SQL_USER and SQL_PASSWORD, which
# temporal-sql-tool reads itself; the password never appears on a command line.
set -eu

SCHEMA_DIR=/etc/temporal/schema/postgresql/v12

temporal-sql-tool --database temporal setup-schema -v 0.0
temporal-sql-tool --database temporal update-schema -d "$SCHEMA_DIR/temporal/versioned"

temporal-sql-tool --database temporal_visibility setup-schema -v 0.0
temporal-sql-tool --database temporal_visibility update-schema -d "$SCHEMA_DIR/visibility/versioned"

echo "Temporal schemas are up to date."
