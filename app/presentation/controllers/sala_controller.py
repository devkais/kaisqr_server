from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field

from app.application.services.gestion_salas import GestionSalas
from app.domain.entities.sala import ContextoDestino
from app.domain.exceptions import (
    AccesoSalaInvalidoError,
    DestinoNoConfiguradoError,
    DocumentoInvalidoError,
    EntregaDestinoError,
    LimiteDocumentosError,
    SalaNoDisponibleError,
    SalaNoEncontradaError,
)


logger = logging.getLogger(__name__)


class CreateSessionRequest(BaseModel):
    sistema: str = Field(min_length=1, max_length=50)
    modulo: str = Field(min_length=1, max_length=50)
    recurso_id: int = Field(gt=0)
    operacion: str = Field(default="subir_archivos", min_length=1, max_length=80)


class JoinSessionRequest(BaseModel):
    codigo: str = Field(pattern=r"^\d{6}$")


def build_router(service: GestionSalas, api_key: str) -> APIRouter:
    router = APIRouter()

    def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
        if api_key and x_api_key != api_key:
            raise HTTPException(status_code=401, detail="API key inválida")

    def require_session_token(
        x_session_token: str | None = Header(default=None, alias="X-Session-Token"),
    ) -> str:
        if not x_session_token:
            raise HTTPException(status_code=401, detail="Token de sesión obligatorio")
        return x_session_token

    @router.post("/salas", dependencies=[Depends(require_api_key)])
    def create_session(request: CreateSessionRequest) -> dict:
        contexto = ContextoDestino(
            sistema=request.sistema,
            modulo=request.modulo,
            recurso_id=request.recurso_id,
            operacion=request.operacion,
        )
        return service.crear_sala(contexto)

    @router.post("/salas/unirse", dependencies=[Depends(require_api_key)])
    def join_session(request: JoinSessionRequest) -> dict:
        try:
            return service.unirse_por_codigo(request.codigo)
        except Exception as error:
            http_error = _to_http_exception(error)
            logger.warning(
                "[HTTP] Unión rechazada | codigo=%s | status=%d | "
                "tipo=%s | detalle=%s",
                request.codigo,
                http_error.status_code,
                type(error).__name__,
                error,
            )
            raise http_error from error

    @router.get("/salas/{sala_id}")
    def get_session(sala_id: str, token: str = Depends(require_session_token)) -> dict:
        try:
            return service.estado_sala(sala_id, token)
        except Exception as error:
            http_error = _to_http_exception(error)
            logger.warning(
                "[HTTP] Consulta de sala rechazada | sala=%s | status=%d | "
                "tipo=%s | detalle=%s",
                sala_id,
                http_error.status_code,
                type(error).__name__,
                error,
            )
            raise http_error from error

    @router.post("/salas/{sala_id}/documentos")
    async def upload_document(
        sala_id: str,
        archivo: UploadFile = File(...),
        token: str = Depends(require_session_token),
        nombre_personalizado: str | None = Form(default=None),
        tipo_archivo: str = Form(default="imagen"),
        poligono: str | None = Form(default=None),
        lote_pdf_id: str | None = Form(default=None),
        nombre_pdf: str | None = Form(default=None),
    ) -> dict:
        try:
            return await service.registrar_documento(
                sala_id=sala_id,
                token=token,
                archivo=archivo,
                nombre_personalizado=nombre_personalizado,
                tipo_archivo=tipo_archivo,
                poligono=poligono,
                lote_pdf_id=lote_pdf_id,
                nombre_pdf=nombre_pdf,
            )
        except Exception as error:
            http_error = _to_http_exception(error)
            logger.warning(
                "[HTTP] Recepción de documento rechazada | sala=%s | "
                "nombre=%s | tipo=%s | status=%d | error=%s | detalle=%s",
                sala_id,
                archivo.filename,
                tipo_archivo,
                http_error.status_code,
                type(error).__name__,
                error,
            )
            raise http_error from error

    @router.post("/salas/{sala_id}/finalizar")
    async def finalize_session(
        sala_id: str,
        token: str = Depends(require_session_token),
    ) -> dict:
        try:
            return await service.finalizar_sala(sala_id, token)
        except Exception as error:
            http_error = _to_http_exception(error)
            logger.warning(
                "[HTTP] Finalización rechazada | sala=%s | status=%d | "
                "error=%s | detalle=%s",
                sala_id,
                http_error.status_code,
                type(error).__name__,
                error,
            )
            raise http_error from error

    @router.websocket("/ws/v1/salas/{sala_id}")
    async def session_websocket(websocket: WebSocket, sala_id: str, token: str = "") -> None:
        try:
            rol = service.validar_acceso(sala_id, token)
            if rol == "movil":
                service.conectar_movil(sala_id, token)
        except Exception:
            await websocket.close(code=1008, reason="Acceso a sala inválido")
            return

        await service.connection_manager.connect(sala_id, websocket)
        await websocket.send_json({"tipo": "sala_conectada", "sala_id": sala_id, "rol": rol})
        try:
            while True:
                mensaje = await websocket.receive_text()
                if mensaje == "ping":
                    await websocket.send_json({"tipo": "pong"})
        except WebSocketDisconnect:
            service.connection_manager.disconnect(sala_id, websocket)
        except Exception:
            service.connection_manager.disconnect(sala_id, websocket)

    return router


def _to_http_exception(error: Exception) -> HTTPException:
    if isinstance(error, SalaNoEncontradaError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, AccesoSalaInvalidoError):
        return HTTPException(status_code=401, detail=str(error))
    if isinstance(error, (SalaNoDisponibleError, LimiteDocumentosError)):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, DocumentoInvalidoError):
        return HTTPException(status_code=413, detail=str(error))
    if isinstance(error, (DestinoNoConfiguradoError, EntregaDestinoError)):
        return HTTPException(status_code=502, detail=str(error))
    return HTTPException(status_code=500, detail="Error interno del servidor")
