# Repository notes

## Local development

- Single Python 3.11+ service. From the repository root: `python -m venv .venv`, then PowerShell `.\.venv\Scripts\Activate.ps1`, then `python -m pip install -r requirements.txt`. Run with `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`.
- `requirements.txt` is the pinned dependency source; no Python lockfile, test suite, CI, or CLI lint/format/typecheck configuration is tracked. `.vscode/settings.json` only enables basic editor type checking. With the server running, smoke-check `Invoke-RestMethod http://localhost:8000/health`; this does not verify destination delivery.
- Settings in `app/core/config.py` load environment variables and root `.env`; relative storage paths also depend on the working directory. `.env` is ignored and no `.env.example` is tracked: skip the README's `Copy-Item .env.example .env` step. Settings are cached and dependencies constructed at import time; restart after configuration changes.

## Wiring and contracts

- `app/main.py` constructs shared dependencies; `app/presentation/controllers/sala_controller.py` builds HTTP/WebSocket routes; `app/application/services/gestion_salas.py` orchestrates Spanish-named domain entities, PDF batches, delivery, and events.
- Default HTTP prefix is `/api/v1`; WebSocket is `/api/v1/ws/v1/salas/{sala_id}` (keep the extra `/v1` to match the current router). Returned socket URLs use the independent `PUBLIC_WEBSOCKET_URL` setting: keep it aligned with the externally reachable host, scheme, and API prefix. `/health` and `/docs` are outside the prefix.
- Creating/joining rooms requires `X-API-Key` (default `development-key`); room HTTP operations use `X-Session-Token`, while WebSocket authentication uses the `token` query parameter. Observer and mobile tokens are distinct; only mobile tokens permit uploads.
- Creation requires exactly one of numeric `recurso_id` or UUID `recurso_referencia`. `DestinationDispatcher` selects by `contexto_destino.sistema`; register new adapters in `app/main.py` and keep backend-specific persistence/contracts in `app/infrastructure/destinations`.

## Storage and delivery gotchas

- Rooms and WebSocket connections are process-local: reload/restart loses rooms, and multiple workers do not share them. Uploaded files default to `storage/temp`; successful finalization deletes the room's files, but failed delivery, closing, and expiry do not perform that cleanup. This service is not durable storage.
- KaisVM supports `grupos`/`gastos` with numeric `recurso_id`. With `KAISVM_FORWARD_ENABLED=false` (default), delivery reports success without forwarding, and finalization still deletes temporary files. Enable forwarding and configure `KAISVM_BASE_URL`/`KAISVM_SERVICE_TOKEN` to verify real delivery.
- Transporte supports `gastos` with UUID `recurso_referencia`; unlike KaisVM, disabled forwarding is an error. Real delivery requires `TRANSPORTE_FORWARD_ENABLED=true`, `TRANSPORTE_BASE_URL`, and a nonempty `TRANSPORTE_SERVICE_KEY` matching the destination backend. Keep this server-to-server credential out of frontend configuration; include the frontend origin in comma-separated `CORS_ORIGINS`.
- Uploads marked `tipo_archivo=pdf` are source images requiring a four-point normalized `poligono`, not pass-through PDFs. Finalization groups pages by `lote_pdf_id` (default `lote_principal`) and generates one PDF per batch.
