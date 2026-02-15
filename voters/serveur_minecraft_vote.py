import logging

from playwright.async_api import Page

from browser import human_delay
from voters.base import BaseVoter

logger = logging.getLogger("auto-voter")


class ServeurMinecraftVoteVoter(BaseVoter):
    """Voter pour serveur-minecraft-vote.fr — nécessite un clic sur le bouton Voter."""

    def __init__(self, pseudo: str, interval_minutes: int = 90, random_delay_max: int = 5):
        super().__init__(
            name="serveur-minecraft-vote.fr",
            pseudo=pseudo,
            link_pattern="serveur-minecraft-vote.fr",
            interval_minutes=interval_minutes,
            random_delay_max=random_delay_max,
        )

    async def _handle_external_vote(self, page: Page) -> bool:
        """Clique sur le bouton Voter sur serveur-minecraft-vote.fr."""
        try:
            logger.debug("[%s] Recherche du bouton Voter sur le site externe", self.name)
            vote_button = page.locator(
                "button:has-text('Voter'), "
                "a:has-text('Voter'), "
                "input[value='Voter']"
            ).first
            await vote_button.wait_for(state="visible", timeout=10000)
            await human_delay(1.0, 2.0)
            await vote_button.click()

            # Attendre confirmation
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
            await human_delay(1.0, 2.0)

            return True

        except Exception as e:
            logger.error("[%s] Erreur sur le site externe: %s", self.name, e)
            return False
