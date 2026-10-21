#!/bin/sh
set -eu
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv app_password PROCTIX_DB_PASSWORD
CREATE ROLE proctix_app LOGIN PASSWORD :'app_password';
GRANT CONNECT ON DATABASE proctix TO proctix_app;
GRANT USAGE ON SCHEMA public TO proctix_app;
ALTER DEFAULT PRIVILEGES FOR ROLE proctix_migrator IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO proctix_app;
ALTER DEFAULT PRIVILEGES FOR ROLE proctix_migrator IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO proctix_app;
SQL
