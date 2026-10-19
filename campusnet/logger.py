"""日志：写入 logs/app.log，按大小轮转。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Any

from .config import DATA_DIR

LOGGER_NAME = "campusnet"


def setup_logging(config: dict[str, Any], console: bool = False) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, str(config["logging"].get("level", "INFO")).upper(), logging.INFO))
    logger.propagate = False
    if logger.handlers:
        return logger

    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=int(config["logging"].get("max_bytes", 1048576)),
        backupCount=int(config["logging"].get("backup_count", 3)),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if console and sys.stdout is not None:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger
