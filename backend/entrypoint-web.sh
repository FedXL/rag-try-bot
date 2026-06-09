set -e
python manage.py migrate --noinput
python manage.py ensure_admin
python manage.py sync_initial_knowledge || true
python manage.py collectstatic --noinput || true
exec gunicorn app.wsgi:application --bind 0.0.0.0:9001 --workers 2 --timeout 600
