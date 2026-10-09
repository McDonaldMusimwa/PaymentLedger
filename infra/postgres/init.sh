#!/bin/bash
# Runs once, on first start of an empty data volume.
# One Postgres instance, one database + role per backend; neither can read the other's data.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
	CREATE ROLE auth LOGIN PASSWORD '${AUTH_DB_PASSWORD}';
	CREATE DATABASE auth_db OWNER auth;
	REVOKE ALL ON DATABASE auth_db FROM PUBLIC;

	CREATE ROLE ledger LOGIN PASSWORD '${LEDGER_DB_PASSWORD}';
	CREATE DATABASE ledger_db OWNER ledger;
	REVOKE ALL ON DATABASE ledger_db FROM PUBLIC;
EOSQL
