# Migrations run BEFORE the server accepts requests: `python -m automation.migrate`
# applies the schema, and the `&&` means uvicorn only starts if it succeeds. So a
# migration failure fails the deploy closed (no server serving an unmigrated DB)
# rather than silently coming up and falling open on the prospect quota.
web: find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null; python -m automation.migrate && exec uvicorn server.api:app --host 0.0.0.0 --port $PORT
