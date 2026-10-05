# Repository notes

- This is a single Python 3.11+ FastAPI service; install dependencies with `python -m pip install -r requirements.txt` and run locally with `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` (PowerShell virtualenv activation is in `README.md`).
- Configuration is loaded from environment variables / `.env` by `app/core/config.py`; `.env` is ignored. There is no tracked `.env.example`, so do not rely on the README's `Copy-Item .env.example .env` step.
- The app is wired in `app/main.py`: routes are under `/api/v1` by default, health is `/health`, API docs are `/docs`, and the WebSocket route is `/api/v1/ws/v1/salas/{sala_id}`.
- `app/domain` contains Spanish-named session entities and rules; `app/application/services/gestion_salas.py` orchestrates use cases; `app/infrastructure` provides in-memory storage, temporary files, QR/PDF generation, WebSockets, and the KaisVM destination adapter; HTTP/WebSocket endpoints live in `app/presentation/controllers`.
- Sessions are held in `MemorySalaRepository` (not durable storage); uploaded files go to `storage/temp` by default and are removed after successful delivery. Keep durable persistence in the destination backend/adapter boundary rather than assuming this service stores sessions permanently.
- Tests, lint, formatting, and type-check configuration are not present in the repository. `requirements.txt` is the dependency source of truth; no lockfile or build manifest is tracked.
