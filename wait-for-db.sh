#!/bin/bash
# wait-for-db.sh - Attendre que PostgreSQL soit prêt

set -e

host="$1"
shift
cmd="$@"

until PGPASSWORD=$DB_PASSWORD psql -h "$host" -U "$DB_USER" -d "$DB_NAME" -c '\q' 2>/dev/null; do
  >&2 echo "⏳ PostgreSQL n'est pas encore prêt - attente..."
  sleep 1
done

>&2 echo "✅ PostgreSQL est prêt - démarrage de l'application"
exec $cmd
