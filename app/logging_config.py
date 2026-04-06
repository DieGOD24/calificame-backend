import sys

from loguru import logger

from app.config import settings


def setup_logging() -> None:
    """Configure loguru for the application."""
    logger.remove()

    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    logger.add(
        sys.stderr,
        format=log_format,
        level=settings.LOG_LEVEL,
        colorize=True,
    )

    logger.add(
        "logs/calificame.log",
        format=log_format,
        level="DEBUG",
        rotation="10 MB",
        retention="30 days",
        compression="gz",
    )
