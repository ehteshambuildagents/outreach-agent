web: find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null; exec uvicorn server.api:app --host 0.0.0.0 --port $PORT
