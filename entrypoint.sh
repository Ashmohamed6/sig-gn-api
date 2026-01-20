#!/bin/bash
set -e

echo "🔄 Waiting for database..."
./wait-for-db.sh db

echo "📦 Collecting static files..."
python manage.py collectstatic --noinput --clear

echo "🚀 Starting Django server..."
exec python manage.py runserver 0.0.0.0:8000