"""
recipeparser.adapters.webapp — a browser UI for running and watching extractions.

    python -m recipeparser.adapters.webapp        (or: recipeparser-web)

Layout:
    server.py      FastAPI app: JSON API, SSE live stream, static frontend
    manager.py     starts runs as child processes; pause/resume/stop/kill/restart; crash detection
    runner.py      one run (child process): preflight, read, pipeline, outputs
    instrument.py  records every model call and log line of a run
    errors.py      explains every failure: code, cause, fix
    settings.py    every environment variable: registry, validation, resolution
    analytics.py   per-run metrics and run comparison
    store.py       the SQLite database everything is recorded in
    static/        the browser UI (plain ES modules, no build step)

Runs are local only: they write Paprika/Cayenne archives and JSON under the
data directory and never touch Supabase.
"""
