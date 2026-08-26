class SalaNoEncontradaError(Exception):
    """La sala solicitada no existe."""


class SalaNoDisponibleError(Exception):
    """La sala expiró o ya no acepta operaciones."""


class AccesoSalaInvalidoError(Exception):
    """El código o token de acceso no es válido."""


class LimiteDocumentosError(Exception):
    """La sala alcanzó el límite de documentos."""


class DocumentoInvalidoError(Exception):
    """El documento no cumple las reglas de la sala."""


class DestinoNoConfiguradoError(Exception):
    """El adaptador del sistema destino no está configurado."""


class EntregaDestinoError(Exception):
    """El backend propietario rechazó la entrega."""

