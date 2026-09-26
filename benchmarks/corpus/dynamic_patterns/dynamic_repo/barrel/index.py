# Re-exports with aliases and indirect routing
from dynamic_repo.plugins.alpha import AlphaPlugin as PrimaryPlugin
from dynamic_repo.handlers.user_handler import UserHandler
from dynamic_repo.dispatcher import DynamicDispatcher

__all__ = ["PrimaryPlugin", "UserHandler", "DynamicDispatcher"]
