"""
Centralized logging setup — rotating file + console.
Import and call setup_logging() once at the top of main.py before anything else.
"""

import logging
import logging.handlers
import os


def setup_logging(log_dir: str | None = None) -> logging.Logger:
    # LOG_DIR env se override kar sakte hain (Render persistent disk ke liye)
    log_dir = log_dir or os.getenv("LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Remove any pre-existing handlers (safe to call multiple times)
    root.handlers.clear()

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # Rotating file handler: max 5 MB × 3 files
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "bot.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    return root
