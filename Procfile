web: bash start.sh
worker: celery -A scheduler.celery_app worker --beat --loglevel=info --concurrency=2
