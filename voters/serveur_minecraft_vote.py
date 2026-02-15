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
        """Remplit le pseudo et clique sur le bouton de vote sur serveur-minecraft-vote.fr."""
        try:
            # 1. Attendre le chargement complet de la page
            try:
                await page.wait_for_load_state("networkidle", timeout=int(10000 * self.timeout_factor))
            except Exception:
                await page.wait_for_load_state("domcontentloaded", timeout=int(10000 * self.timeout_factor))

            # 2. Remplir le champ pseudo si visible (pré-rempli via ?pseudo= dans l'URL)
            logger.debug("%s Recherche du champ pseudo sur le site externe", self.log_prefix)
            pseudo_input = page.locator("#pseudo")
            try:
                await pseudo_input.wait_for(state="visible", timeout=int(5000 * self.timeout_factor))
                current_value = await pseudo_input.input_value()
                if not current_value or current_value != self.pseudo:
                    await pseudo_input.click()
                    await pseudo_input.fill(self.pseudo)
                    logger.debug("%s Pseudo '%s' saisi dans le champ du site de vote", self.log_prefix, self.pseudo)
                else:
                    logger.debug("%s Pseudo déjà pré-rempli via l'URL", self.log_prefix)
                await human_delay(0.3, 0.6)
            except Exception:
                logger.debug("%s Champ pseudo non trouvé ou déjà rempli", self.log_prefix)

            # 3. Cliquer sur le bouton de vote (#vote-action)
            logger.debug("%s Recherche du bouton de vote (#vote-action)", self.log_prefix)
            vote_button = page.locator("#vote-action")
            try:
                await vote_button.wait_for(state="visible", timeout=int(10000 * self.timeout_factor))
            except Exception:
                # Fallback : chercher par texte si l'ID n'est pas trouvé
                logger.debug("%s #vote-action non trouvé, recherche par texte", self.log_prefix)
                vote_button = page.locator(
                    "button:has-text('Voter'), "
                    "a:has-text('Voter'), "
                    "input[value*='Voter']"
                ).first
                await vote_button.wait_for(state="visible", timeout=int(5000 * self.timeout_factor))

            await human_delay(0.3, 0.6)
            await vote_button.click()
            logger.debug("%s Bouton de vote cliqué", self.log_prefix)

            # 4. Vérifier la réponse via les messages toast
            await human_delay(1.0, 2.0)
            toast = page.locator(".toast-container")
            try:
                await toast.wait_for(state="visible", timeout=int(10000 * self.timeout_factor))
                toast_text = await toast.inner_text()
                logger.debug("%s Toast détecté: %s", self.log_prefix, toast_text[:100])

                if "devez attendre" in toast_text.lower() or "attendre" in toast_text.lower():
                    logger.info("%s Vote en cooldown sur le site externe: %s", self.log_prefix, toast_text.strip()[:80])
                    return False

                if "licitation" in toast_text or "merci" in toast_text.lower() or "succ" in toast_text.lower():
                    logger.debug("%s Confirmation de vote détectée", self.log_prefix)
                    await human_delay(1.0, 2.0)
                    return True
            except Exception:
                logger.debug("%s Pas de toast détecté, vérification alternative", self.log_prefix)

            # 5. Fallback : chercher un message de confirmation dans la page
            try:
                confirmation = page.locator(
                    "text=/[Ff]élicitation|[Vv]ote.*enregistr|[Vv]ote.*comptabilis|[Mm]erci|[Vv]ote.*réussi/"
                ).first
                await confirmation.wait_for(state="visible", timeout=int(5000 * self.timeout_factor))
                logger.debug("%s Message de confirmation détecté dans la page", self.log_prefix)
            except Exception:
                logger.debug("%s Pas de confirmation explicite, le vote a probablement été envoyé", self.log_prefix)

            # 6. Délai final pour s'assurer que le vote est bien enregistré côté serveur
            await human_delay(2.0, 4.0)
            return True

        except Exception as e:
            logger.error("%s Erreur sur le site externe: %s", self.log_prefix, e)
            return False
