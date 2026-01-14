from typing import Dict, Type
from .interface import AbstractBotCustomization
from .default import DefaultBotCustomization

# Will be populated by imports to avoid circular deps
# Mapping of bot_id -> Customization Class
_CUSTOMIZATION_REGISTRY: Dict[int, Type[AbstractBotCustomization]] = {}


def register_customization(bot_id: int):
    def decorator(cls: Type[AbstractBotCustomization]):
        _CUSTOMIZATION_REGISTRY[bot_id] = cls
        return cls
    return decorator


def get_customization(bot_id: int) -> AbstractBotCustomization:
    """Returns an instance of the customization for the given bot_id, or Default."""
    customization_cls = _CUSTOMIZATION_REGISTRY.get(bot_id, DefaultBotCustomization)
    return customization_cls()
