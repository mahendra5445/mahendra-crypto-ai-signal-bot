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
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    # Console handler — always on (Railway/Render scrape stdout) (M-012)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # File handler only when LOG_DIR is explicitly set (mounted volume).
    # Default "logs" on ephemeral disk vanishes every deploy and is skipped
    # unless ENABLE_FILE_LOGS=1.
    enable_file = os.getenv("ENABLE_FILE_LOGS", "").strip() in ("1", "true", "True")
    explicit_dir = os.getenv("LOG_DIR")
    if explicit_dir or enable_file:
        try:
            os.makedirs(log_dir, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                os.path.join(log_dir, "bot.log"),
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError as e:
            logging.getLogger(__name__).warning(
                f"[LOG] File logging disabled ({e}) — console only"
            )
    else:
        # Note once via basicConfig-less path after handlers exist
        pass

    return root
