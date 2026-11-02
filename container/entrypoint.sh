#!/bin/sh
set -eu

case "${1:-web}" in
web)
    # The database may still be starting. Give up eventually so real errors surface.
    attempt=0
    until python manage.py migrate --noinput; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 30 ]; then
            echo "Database migration failed after $attempt attempts" >&2
            exit 1
        fi
        sleep 2
    done
    python manage.py bootstrap
    exec gunicorn bundleui.wsgi:application \
        --bind 0.0.0.0:8000 \
        --workers "${GUNICORN_WORKERS:-2}" \
        --threads "${GUNICORN_THREADS:-4}" \
        --timeout "${GUNICORN_TIMEOUT:-180}" \
        --worker-tmp-dir /dev/shm \
        --access-logfile - \
        --error-logfile -
    ;;
sync)
    # The web container applies migrations; wait for it.
    until python manage.py migrate --check >/dev/null 2>&1; do
        echo "Waiting for database migrations..."
        sleep 3
    done
    exec python manage.py sync_worker
    ;;
*)
    exec "$@"
    ;;
esac
