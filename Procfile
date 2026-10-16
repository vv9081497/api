web: gunicorn -k gthread -w 2 --threads 8 --timeout 120 --max-requests 500 --max-requests-jitter 50 autoshopify:app
