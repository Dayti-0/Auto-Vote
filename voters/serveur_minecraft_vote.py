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
        """Remplit le pseudo et clique sur 'Voter en étant déconnecté' sur serveur-minecraft-vote.fr."""
        try:
            # 1. Remplir le champ pseudo sur la page du site de vote
            logger.debug("%s Recherche du champ pseudo sur le site externe", self.log_prefix)
            pseudo_input = page.locator("input#pseudo")
            try:
                await pseudo_input.wait_for(state="visible", timeout=5000)
                await pseudo_input.click()
                await pseudo_input.fill(self.pseudo)
                logger.debug("%s Pseudo '%s' saisi dans le champ du site de vote", self.log_prefix, self.pseudo)
                await human_delay(0.2, 0.4)
            except Exception:
                logger.debug("%s Champ pseudo non trouvé ou déjà rempli", self.log_prefix)

            # 2. Cliquer sur le bouton "Voter en étant déconnecté"
            logger.debug("%s Recherche du bouton 'Voter en étant déconnecté'", self.log_prefix)
            vote_button = page.locator(
                "button:has-text('Voter en étant déconnecté'), "
                "a:has-text('Voter en étant déconnecté'), "
                "input[value*='Voter en étant déconnecté']"
            ).first
            await vote_button.wait_for(state="visible", timeout=5000)
            await human_delay(0.3, 0.6)
            await vote_button.click()

            # 3. Attendre confirmation
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
            await human_delay(0.3, 0.6)

            logger.debug("%s Bouton 'Voter en étant déconnecté' cliqué avec succès", self.log_prefix)
            return True

        except Exception as e:
            logger.error("%s Erreur sur le site externe: %s", self.log_prefix, e)
            return False
