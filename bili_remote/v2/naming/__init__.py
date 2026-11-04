"""Isolated Bili Remote V2 naming module; no app registration side effects."""
from .errors import NamingError
from .service import NamingService
from .router import create_router

__all__ = ["NamingService", "NamingError", "create_router"]
