import logging

from playwright.async_api import Page

from browser import human_delay
from voters.base import BaseVoter

logger = logging.getLogger("auto-voter")


class ServeurPriveVoter(BaseVoter):
    """Voter pour serveur-prive.net — le vote est comptabilisé au chargement de la page."""

    def __init__(self, interval_minutes: int = 90, random_delay_max: int = 5):
        super().__init__(
            name="serveur-prive.net",
            url="https://serveur-prive.net/minecraft/survivalworld/vote",
            interval_minutes=interval_minutes,
            random_delay_max=random_delay_max,
        )

    async def vote(self, page: Page) -> bool:
        """Navigue vers la page — le vote est comptabilisé au chargement."""
        try:
            logger.debug("[%s] Navigation vers %s", self.name, self.url)
            await page.goto(self.url, wait_until="networkidle")
            await human_delay(2.0, 4.0)

            self.record_success()
            return True

        except Exception as e:
            self.record_failure(str(e))
            return False
