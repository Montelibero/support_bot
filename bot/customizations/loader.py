from aiogram import Router
from .registry import _CUSTOMIZATION_REGISTRY, get_customization

def get_all_routers() -> Router:
    """
    Returns a master router containing all customization routers.
    This should be called AFTER all customizations are imported.
    """
    master_router = Router()
    
    # We need to instantiate customizations to get their routers
    # Since specific bot logic might be needed, we iterate through registered classes
    # But wait, get_customization returns a fresh instance. 
    # Routers should ideally be singleton-ish or at least stateless regarding the bot instance if possible
    # or the customization instance handles it.
    
    # For now, let's just make sure we import all known customizations here
    # so they register themselves.
    
    # Import known customizations
    from . import helper
    from . import test_customization
    
    for bot_id in _CUSTOMIZATION_REGISTRY:
        customization = get_customization(bot_id)
        # We only add the router if it has handlers (not empty)
        # But even empty routers are fine.
        master_router.include_router(customization.router)
        
    return master_router
