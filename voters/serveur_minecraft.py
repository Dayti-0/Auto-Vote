import logging

from playwright.async_api import Page

from browser import human_delay
from voters.base import BaseVoter

logger = logging.getLogger("auto-voter")


class ServeurMinecraftVoter(BaseVoter):
    """Voter pour serveur-minecraft.com — le vote est comptabilisé au chargement de la page."""

    def __init__(self, pseudo: str, interval_minutes: int = 180, random_delay_max: int = 5):
        super().__init__(
            name="serveur-minecraft.com",
            pseudo=pseudo,
            link_pattern="serveur-minecraft.com",
            interval_minutes=interval_minutes,
            random_delay_max=random_delay_max,
        )

    async def _handle_external_vote(self, page: Page) -> bool:
        """Le vote est comptabilisé au chargement — on attend juste le chargement."""
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
            await human_delay(2.0, 4.0)

            return True

        except Exception as e:
            logger.error("[%s] Erreur sur le site externe: %s", self.name, e)
            return False
