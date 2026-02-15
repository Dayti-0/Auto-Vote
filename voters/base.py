import logging
from abc import ABC, abstractmethod
from datetime import datetime

logger = logging.getLogger("auto-voter")


class BaseVoter(ABC):
    """Classe abstraite pour les voters."""

    def __init__(self, name: str, url: str, interval_minutes: int, random_delay_max: int):
        self.name = name
        self.url = url
        self.interval_minutes = interval_minutes
        self.random_delay_max = random_delay_max
        self.last_vote_time: datetime | None = None
        self.vote_count: int = 0
        self.consecutive_failures: int = 0

    @abstractmethod
    async def vote(self, page) -> bool:
        """Effectue le vote. Retourne True si succès, False sinon."""
        pass

    def record_success(self):
        """Enregistre un vote réussi."""
        self.vote_count += 1
        self.consecutive_failures = 0
        self.last_vote_time = datetime.now()
        logger.info(
            "[%s] Vote #%d réussi",
            self.name, self.vote_count,
        )

    def record_failure(self, error: str):
        """Enregistre un échec de vote."""
        self.consecutive_failures += 1
        self.last_vote_time = datetime.now()
        if self.consecutive_failures >= 3:
            logger.warning(
                "[%s] %d échecs consécutifs ! Dernière erreur: %s",
                self.name, self.consecutive_failures, error,
            )
        else:
            logger.error(
                "[%s] Échec du vote (%s)",
                self.name, error,
            )
