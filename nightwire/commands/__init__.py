"""Command handler framework for Nightwire bot.

Provides the BaseCommandHandler ABC, BotContext dependency container,
and HandlerRegistry for mapping command names to async handlers.
"""

from .base import BUILTIN_COMMANDS, BaseCommandHandler, BotContext, HandlerRegistry

__all__ = [
    "BaseCommandHandler",
    "BotContext",
    "HandlerRegistry",
    "BUILTIN_COMMANDS",
]
