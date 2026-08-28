import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.application.services.gestion_salas import GestionSalas
from app.core.config import get_settings
from app.infrastructure.destinations.kaisvm_adapter import KaisvmAdapter
from app.infrastructure.documents.pdf_creator import PdfCreator
from app.infrastructure.qr.qr_code_generator import QrCodeGenerator
from app.infrastructure.repositories.memory_sala_repository import MemorySalaRepository
from app.infrastructure.storage.temporary_file_storage import TemporaryFileStorage
from app.infrastructure.websocket.connection_manager import ConnectionManager
from app.presentation.controllers.sala_controller import build_router


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()
repository = MemorySalaRepository()
connection_manager = ConnectionManager()
temporary_storage = TemporaryFileStorage(settings.temporary_storage_path)
destination_adapter = KaisvmAdapter(settings)
pdf_creator = PdfCreator(settings.temporary_storage_path)
session_service = GestionSalas(
    repository=repository,
    qr_generator=QrCodeGenerator(),
    temporary_storage=temporary_storage,
    destination_adapter=destination_adapter,
    pdf_creator=pdf_creator,
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

logger.info(
    "[INICIO] KaisQr_server | entorno=%s | api=%s | reenvio_kaisvm=%s | "
    "destino=%s | almacenamiento_temporal=%s",
    settings.environment,
    settings.api_prefix,
    settings.kaisvm_forward_enabled,
    settings.kaisvm_base_url,
    settings.temporary_storage_path,
)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}
