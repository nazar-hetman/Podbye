"""Models package for Vigil."""
from app.models.finding import Finding
from app.models.smart_entity import SmartEntity, ENTITY_TYPES

__all__ = [
    "Finding",
    "SmartEntity",
    "ENTITY_TYPES",
]
