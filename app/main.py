from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.application.services.gestion_salas import GestionSalas
from app.core.config import get_settings
from app.infrastructure.destinations.kaisvm_adapter import KaisvmAdapter
from app.infrastructure.qr.qr_code_generator import QrCodeGenerator
from app.infrastructure.repositories.memory_sala_repository import MemorySalaRepository
from app.infrastructure.storage.temporary_file_storage import TemporaryFileStorage
from app.infrastructure.websocket.connection_manager import ConnectionManager
from app.presentation.controllers.sala_controller import build_router


settings = get_settings()
repository = MemorySalaRepository()
connection_manager = ConnectionManager()
temporary_storage = TemporaryFileStorage(settings.temporary_storage_path)
destination_adapter = KaisvmAdapter(settings)
session_service = GestionSalas(
    repository=repository,
    qr_generator=QrCodeGenerator(),
    temporary_storage=temporary_storage,
    destination_adapter=destination_adapter,
    connection_manager=connection_manager,
    settings=settings,
)

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(build_router(session_service, settings.api_key), prefix=settings.api_prefix)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}

