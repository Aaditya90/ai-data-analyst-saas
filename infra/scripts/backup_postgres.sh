#!/usr/bin/env bash
# Phase 18: logical Postgres backup for the production compose stack.
#   bash infra/scripts/backup_postgres.sh [output_dir]
# Writes a compressed custom-format dump (restorable with pg_restore, and
# selectively — e.g. one table). Run from the repo root with
# .env.production present. Schedule with cron; copy dumps off-host.
# Restore: see README "Backups and restore".
set -euo pipefail
OUT_DIR="${1:-./backups}"
mkdir -p "$OUT_DIR"
set -a; . ./.env.production; set +a
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$OUT_DIR/${POSTGRES_DB}-${STAMP}.dump"
docker compose --env-file .env.production -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --compress=6 > "$FILE"
# A truncated dump (e.g. container died mid-stream) must not look like a good backup.
[ -s "$FILE" ] || { echo "backup is empty, removing" >&2; rm -f "$FILE"; exit 1; }
echo "wrote $FILE ($(du -h "$FILE" | cut -f1))"
