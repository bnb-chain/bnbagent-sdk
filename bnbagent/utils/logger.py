"""
Logger utility module.

Provides a unified logging interface for the ERC8004Agent SDK.
"""

import logging
from typing import Optional


def get_logger(name: str, debug: bool = False) -> logging.Logger:
    """
    Get a logger instance with unified configuration.

    Args:
        name: Logger name (typically __name__ or module name)
        debug: If True, set log level to DEBUG; otherwise WARNING

    Returns:
        logging.Logger: Configured logger instance

    Example:
        >>> logger = get_logger(__name__, debug=True)
        >>> logger.info("This is an info message")
        >>> logger.debug("This is a debug message")
    """
    logger = logging.getLogger(name)

    # Only configure if logger doesn't have handlers (avoid duplicate handlers)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # Set log level based on debug flag
    if debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.WARNING)

    return logger
