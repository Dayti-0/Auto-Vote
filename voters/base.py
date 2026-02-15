import logging
from abc import ABC, abstractmethod
from datetime import datetime

from browser import human_delay

logger = logging.getLogger("auto-voter")

SURVIVALWORLD_VOTE_URL = "https://survivalworld.fr/vote"


class BaseVoter(ABC):
    """Classe abstraite pour les voters. Passe par survivalworld.fr/vote."""

    def __init__(self, name: str, pseudo: str, link_pattern: str,
                 interval_minutes: int, random_delay_max: int):
        self.name = name
        self.pseudo = pseudo
        self.link_pattern = link_pattern  # Pattern pour trouver le lien de vote sur survivalworld.fr
        self.interval_minutes = interval_minutes
        self.random_delay_max = random_delay_max
        self.last_vote_time: datetime | None = None
        self.vote_count: int = 0
        self.consecutive_failures: int = 0

    async def vote(self, page) -> bool:
        """Effectue le vote en passant par survivalworld.fr/vote."""
        try:
            # 1. Naviguer vers survivalworld.fr/vote
            logger.debug("[%s] Navigation vers %s", self.name, SURVIVALWORLD_VOTE_URL)
            await page.goto(SURVIVALWORLD_VOTE_URL, wait_until="domcontentloaded")
            await human_delay(2.0, 4.0)

            # 1b. Gérer la popup de cookies si elle apparaît
            try:
                cookie_btn = page.locator(
                    "button:has-text('Autoriser'), "
                    "a:has-text('Autoriser')"
                ).first
                await cookie_btn.wait_for(state="visible", timeout=3000)
                await human_delay(0.5, 1.0)
                await cookie_btn.click()
                logger.debug("[%s] Popup de cookies acceptée", self.name)
                await human_delay(1.0, 2.0)
            except Exception:
                logger.debug("[%s] Pas de popup de cookies détectée", self.name)

            # 2. Entrer le pseudo si le champ est visible (pas connecté)
            try:
                pseudo_input = page.locator("input[type='text']").first
                is_visible = await pseudo_input.is_visible()
                if is_visible:
                    await pseudo_input.click()
                    await pseudo_input.fill("")
                    await pseudo_input.type(self.pseudo, delay=80)
                    await human_delay(0.5, 1.0)

                    # 3. Cliquer sur Continuer
                    continuer_btn = page.locator(
                        "button:has-text('Continuer'), "
                        "input[type='submit'][value*='ontinuer'], "
                        "a:has-text('Continuer')"
                    ).first
                    await continuer_btn.click()
                    logger.debug("[%s] Pseudo '%s' saisi et Continuer cliqué", self.name, self.pseudo)
                    await human_delay(2.0, 4.0)
                else:
                    logger.debug("[%s] Champ pseudo non visible, session active", self.name)
            except Exception:
                logger.debug("[%s] Champ pseudo non trouvé, session peut-être active", self.name)

            # 4. Attendre le chargement complet de la page
            await page.wait_for_load_state("domcontentloaded")
            await human_delay(1.0, 2.0)

            # 5. Trouver le lien de vote correspondant par pattern d'URL
            vote_link = page.locator(f"a[href*='{self.link_pattern}']").first
            try:
                await vote_link.wait_for(state="visible", timeout=10000)
            except Exception:
                logger.error(
                    "[%s] Lien de vote introuvable (pattern: %s)",
                    self.name, self.link_pattern,
                )
                self.record_failure(f"Lien de vote introuvable (pattern: {self.link_pattern})")
                return False

            logger.debug("[%s] Lien de vote trouvé, clic en cours...", self.name)
            await human_delay(1.0, 2.0)

            # 6. Cliquer sur le lien de vote (ouvre un nouvel onglet)
            async with page.context.expect_page() as new_page_info:
                await vote_link.click()

            new_page = await new_page_info.value
            await new_page.wait_for_load_state("domcontentloaded")
            await human_delay(1.5, 3.0)

            # 7. Effectuer le vote sur le site externe
            logger.debug("[%s] Gestion du vote sur le site externe...", self.name)
            success = await self._handle_external_vote(new_page)

            # 8. Fermer l'onglet externe
            if not new_page.is_closed():
                await new_page.close()
            await human_delay(1.5, 3.0)

            # 9. Gérer la popup de confirmation sur survivalworld.fr
            try:
                fermer_btn = page.locator(
                    "button:has-text('Fermer'), "
                    "a:has-text('Fermer')"
                ).first
                await fermer_btn.wait_for(state="visible", timeout=5000)
                await human_delay(0.5, 1.0)
                await fermer_btn.click()
                logger.debug("[%s] Popup de confirmation fermée", self.name)
            except Exception:
                logger.debug("[%s] Pas de popup de confirmation détectée", self.name)

            if success:
                self.record_success()
            else:
                self.record_failure("Échec du vote sur le site externe")

            return success

        except Exception as e:
            self.record_failure(str(e))
            return False

    @abstractmethod
    async def _handle_external_vote(self, page) -> bool:
        """Gère le vote sur le site externe (nouvel onglet). Retourne True si succès."""
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
