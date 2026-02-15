import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger(log_level: str = "INFO", log_file: str = "logs/votes.log") -> logging.Logger:
    """Configure le logging avec double sortie : console (coloré) + fichier rotatif."""
    logger = logging.getLogger("auto-voter")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    if logger.handlers:
        return logger

    # Format commun
    fmt = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # Handler console avec couleurs ANSI
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    console_handler.setFormatter(ColorFormatter(fmt, datefmt))
    logger.addHandler(console_handler)

    # Handler fichier rotatif
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=1 * 1024 * 1024,  # 1 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(fmt, datefmt))
    logger.addHandler(file_handler)

    return logger


class ColorFormatter(logging.Formatter):
    """Formatter avec couleurs ANSI pour la console."""

    COLORS = {
        logging.DEBUG: "\033[36m",     # Cyan
        logging.INFO: "\033[32m",      # Vert
        logging.WARNING: "\033[33m",   # Jaune
        logging.ERROR: "\033[31m",     # Rouge
        logging.CRITICAL: "\033[1;31m",  # Rouge gras
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelno, self.RESET)
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)
