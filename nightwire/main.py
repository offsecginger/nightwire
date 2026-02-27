"""Main entry point for nightwire.

Initializes logging in two phases (defaults then config-driven),
creates the SignalBot, and runs the async event loop with graceful
shutdown on SIGTERM/SIGINT. Supports both Unix signal handlers and
Windows SIGINT fallback.

Key functions:
    main: Async entry point -- sets up logging, config, bot, and
        signal handlers, then runs the message polling loop.
    run: Synchronous wrapper that calls asyncio.run(main()).
"""

import asyncio
import signal
import sys

import structlog

from .logging_config import setup_logging


async def main():
    """Main async entry point."""
    # Phase 1: defaults, cache_logger_on_first_use=False
    setup_logging()
    logger = structlog.get_logger("nightwire")

    logger.info("nightwire_starting", version="1.5.0")

    # Import here to ensure logging is configured first
    from .bot import SignalBot
    from .config import get_config

    config = get_config()
    config.validate()

    # Phase 2: reconfigure with real config, cache_logger_on_first_use=True
    setup_logging(config)

    bot = SignalBot()

    # Setup graceful shutdown
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def handle_shutdown(sig):
        logger.info("shutdown_signal_received", signal=sig.name)
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, handle_shutdown, sig)
        except NotImplementedError:
            # Windows: add_signal_handler not supported.
            # Fall back to signal.signal for SIGINT (Ctrl+C).
            if sig == signal.SIGINT:
                signal.signal(
                    signal.SIGINT,
                    lambda s, f: handle_shutdown(signal.SIGINT),
                )

    # Run the bot
    try:
        bot_task = asyncio.create_task(bot.run())

        # Wait for shutdown signal
        await shutdown_event.wait()

        # Cancel the bot task
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass

    except Exception as e:
        logger.error("bot_error", error=str(e))
        raise
    finally:
        await bot.stop()
        logger.info("nightwire_stopped")


def run():
    """Synchronous entry point for the ``nightwire`` console script."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except SystemExit as e:
        # Propagate exit code (e.g., 75 for update restart)
        sys.exit(e.code)


if __name__ == "__main__":
    run()
