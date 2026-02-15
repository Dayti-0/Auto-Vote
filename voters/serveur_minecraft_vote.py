import logging

from playwright.async_api import Page

from browser import human_delay
from voters.base import BaseVoter

logger = logging.getLogger("auto-voter")


class ServeurMinecraftVoteVoter(BaseVoter):
    """Voter pour serveur-minecraft-vote.fr — nécessite un clic sur le bouton Voter."""

    def __init__(self, pseudo: str, interval_minutes: int = 90, random_delay_max: int = 5):
        url = f"https://serveur-minecraft-vote.fr/serveurs/survivalworld.229/vote?pseudo={pseudo}"
        super().__init__(
            name="serveur-minecraft-vote.fr",
            url=url,
            interval_minutes=interval_minutes,
            random_delay_max=random_delay_max,
        )

    async def vote(self, page: Page) -> bool:
        """Navigue vers la page et clique sur le bouton Voter."""
        try:
            logger.debug("[%s] Navigation vers %s", self.name, self.url)
            await page.goto(self.url, wait_until="domcontentloaded")
            await human_delay(1.5, 3.0)

            # Chercher le bouton "Voter"
            vote_button = page.locator("button:has-text('Voter'), a:has-text('Voter'), input[value='Voter']").first
            await vote_button.wait_for(state="visible", timeout=10000)
            await human_delay(1.0, 2.0)
            await vote_button.click()

            # Attendre confirmation (changement de page ou message de succès)
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
            await human_delay(1.0, 2.0)

            self.record_success()
            return True

        except Exception as e:
            self.record_failure(str(e))
            return False
