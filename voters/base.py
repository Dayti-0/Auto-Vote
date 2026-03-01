import logging
import time
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
        self.next_vote_available: datetime | None = None
        self.last_error: str | None = None
        # Multiplicateur de timeouts (2.0 pour les comptes via proxy)
        self.timeout_factor: float = 1.0

    @property
    def log_prefix(self) -> str:
        """Préfixe pour les logs : [pseudo][site]."""
        return f"[{self.pseudo}][{self.name}]"

    # Sous-classes peuvent mettre quick_close = True pour fermer la page externe immédiatement
    quick_close = False

    async def _handle_cookie_consent(self, page) -> bool:
        """Gère la popup de consentement cookies (CMP) si elle apparaît.

        Essaie plusieurs sélecteurs courants pour les CMP (Quantcast, Didomi, etc.)
        ainsi que les boutons directs sur la page et dans les iframes.
        Retourne True si une popup a été trouvée et acceptée.
        """
        # Sélecteurs pour le bouton "Autoriser" / "Accepter" sur la page principale
        main_page_selectors = [
            "button:has-text('Autoriser')",
            "a:has-text('Autoriser')",
            "button:has-text('Accepter')",
            "button:has-text('Tout accepter')",
            "button:has-text('Accept all')",
            "button:has-text('Consent')",
            # CMP Didomi
            "#didomi-notice-agree-button",
            # CMP Quantcast / autres
            "button.css-47sehv",  # Quantcast "Autoriser" button
            "[aria-label='Autoriser']",
            "[aria-label='Accepter']",
        ]

        # 1. Essayer les boutons sur la page principale
        for selector in main_page_selectors:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible():
                    await human_delay(0.2, 0.5)
                    await btn.click()
                    logger.debug("%s Popup cookies acceptée via: %s", self.log_prefix, selector)
                    await human_delay(0.3, 0.5)
                    return True
            except Exception:
                continue

        # 2. Essayer dans les iframes (les CMP utilisent souvent un iframe)
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            for selector in main_page_selectors:
                try:
                    btn = frame.locator(selector).first
                    if await btn.is_visible():
                        await human_delay(0.2, 0.5)
                        await btn.click()
                        logger.debug(
                            "%s Popup cookies acceptée dans iframe via: %s",
                            self.log_prefix, selector,
                        )
                        await human_delay(0.3, 0.5)
                        return True
                except Exception:
                    continue

        return False

    async def vote(self, page) -> bool:
        """Effectue le vote en passant par survivalworld.fr/vote."""
        try:
            # 1. Naviguer vers survivalworld.fr/vote
            logger.debug("%s Navigation vers %s", self.log_prefix, SURVIVALWORLD_VOTE_URL)
            await page.goto(SURVIVALWORLD_VOTE_URL, wait_until="domcontentloaded")
            await human_delay(0.3, 0.8)

            # 1b. Gérer la popup de cookies si elle apparaît
            # Attendre un peu que le CMP se charge (ils sont souvent asynchrones)
            try:
                await page.wait_for_timeout(int(1500 * self.timeout_factor))
            except Exception:
                pass
            cookie_handled = await self._handle_cookie_consent(page)
            if not cookie_handled:
                logger.debug("%s Pas de popup de cookies détectée", self.log_prefix)

            # 2. Entrer le pseudo si le champ est visible (pas connecté)
            try:
                pseudo_input = page.locator("input[type='text']").first
                is_visible = await pseudo_input.is_visible()
                if is_visible:
                    await pseudo_input.click()
                    await pseudo_input.fill(self.pseudo)
                    await human_delay(0.2, 0.4)

                    # 3. Cliquer sur Continuer
                    continuer_btn = page.locator(
                        "button:has-text('Continuer'), "
                        "input[type='submit'][value*='ontinuer'], "
                        "a:has-text('Continuer')"
                    ).first
                    await continuer_btn.click()
                    logger.debug("%s Pseudo '%s' saisi et Continuer cliqué", self.log_prefix, self.pseudo)
                    await human_delay(0.5, 1.0)

                    # 3b. La popup cookies peut apparaître après le clic sur Continuer
                    await self._handle_cookie_consent(page)
                else:
                    logger.debug("%s Champ pseudo non visible, session active", self.log_prefix)
            except Exception:
                logger.debug("%s Champ pseudo non trouvé, session peut-être active", self.log_prefix)

            # 4. Attendre le chargement complet de la page
            await page.wait_for_load_state("domcontentloaded")
            await human_delay(0.3, 0.6)

            # 5. Trouver le lien de vote correspondant par pattern d'URL
            vote_link = page.locator(f"a[href*='{self.link_pattern}']").first
            try:
                await vote_link.wait_for(state="visible", timeout=int(10000 * self.timeout_factor))
            except Exception:
                logger.error(
                    "%s Lien de vote introuvable (pattern: %s)",
                    self.log_prefix, self.link_pattern,
                )
                self.record_failure(f"Lien de vote introuvable (pattern: {self.link_pattern})")
                return False

            logger.debug("%s Lien de vote trouvé, vérification du cooldown...", self.log_prefix)
            await human_delay(0.3, 0.6)

            # 5b. Vérifier si le bouton est en cooldown (classe "disabled")
            force_click = False
            is_disabled = await vote_link.evaluate("el => el.classList.contains('disabled')")
            if is_disabled:
                # Attendre que le JS active le bouton (cooldown peut expirer pendant le chargement)
                try:
                    enabled_link = page.locator(
                        f"a[href*='{self.link_pattern}']:not(.disabled)"
                    ).first
                    await enabled_link.wait_for(state="visible", timeout=int(10000 * self.timeout_factor))
                    vote_link = enabled_link
                    logger.debug("%s Bouton de vote activé après attente", self.log_prefix)
                except Exception:
                    # Bouton toujours désactivé — vérifier data-vote-time
                    vote_time_str = await vote_link.get_attribute("data-vote-time")
                    if vote_time_str:
                        try:
                            vote_time_ms = int(vote_time_str)
                            now_ms = int(time.time() * 1000)
                            remaining_ms = vote_time_ms - now_ms
                            if remaining_ms > 0:
                                remaining_min = remaining_ms / 60000
                                self.next_vote_available = datetime.fromtimestamp(
                                    vote_time_ms / 1000
                                )
                                logger.info(
                                    "%s Vote en cooldown, disponible dans %.0f min (à %s)",
                                    self.log_prefix,
                                    remaining_min,
                                    self.next_vote_available.strftime("%H:%M"),
                                )
                                # Pas de record_failure : le cooldown est un comportement normal
                                return False
                        except ValueError:
                            pass
                    # data-vote-time absent ou expiré — tenter un clic forcé
                    logger.warning(
                        "%s Bouton marqué désactivé, tentative de clic forcé",
                        self.log_prefix,
                    )
                    force_click = True

            # 6. Cliquer sur le lien de vote (ouvre un nouvel onglet)
            async with page.context.expect_page() as new_page_info:
                await vote_link.click(force=force_click)

            new_page = await new_page_info.value
            await new_page.wait_for_load_state("domcontentloaded")

            if self.quick_close:
                # Vote comptabilisé au chargement — attendre la popup "Fermer"
                # sur survivalworld.fr AVANT de fermer l'onglet externe.
                # Si on ferme/actualise trop tôt, le vote n'est pas pris en compte.
                logger.debug(
                    "%s Page externe chargée, attente de la popup de confirmation...",
                    self.log_prefix,
                )
                try:
                    fermer_btn = page.locator(
                        "button:has-text('Fermer'), "
                        "a:has-text('Fermer')"
                    ).first
                    await fermer_btn.wait_for(
                        state="visible",
                        timeout=int(30000 * self.timeout_factor),
                    )
                    await human_delay(0.3, 0.6)
                    await fermer_btn.click()
                    logger.debug("%s Popup de confirmation fermée", self.log_prefix)
                    success = True
                except Exception:
                    logger.warning(
                        "%s Popup 'Fermer' non détectée après 30s",
                        self.log_prefix,
                    )
                    success = False

                # Fermer l'onglet externe après la confirmation
                if not new_page.is_closed():
                    await new_page.close()
            else:
                await human_delay(0.3, 0.6)

                # 7. Effectuer le vote sur le site externe
                logger.debug("%s Gestion du vote sur le site externe...", self.log_prefix)
                success = await self._handle_external_vote(new_page)

                # 8. Fermer l'onglet externe
                if not new_page.is_closed():
                    await new_page.close()

                # 9. Gérer la popup de confirmation sur survivalworld.fr
                try:
                    fermer_btn = page.locator(
                        "button:has-text('Fermer'), "
                        "a:has-text('Fermer')"
                    ).first
                    await fermer_btn.wait_for(state="visible", timeout=int(5000 * self.timeout_factor))
                    await fermer_btn.click()
                    logger.debug("%s Popup de confirmation fermée", self.log_prefix)
                except Exception:
                    logger.debug("%s Pas de popup de confirmation détectée", self.log_prefix)

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
        self.last_error = None
        logger.info(
            "%s Vote #%d réussi",
            self.log_prefix, self.vote_count,
        )

    def record_failure(self, error: str):
        """Enregistre un échec de vote."""
        self.consecutive_failures += 1
        self.last_vote_time = datetime.now()
        self.last_error = error
        if self.consecutive_failures >= 3:
            logger.warning(
                "%s %d échecs consécutifs ! Dernière erreur: %s",
                self.log_prefix, self.consecutive_failures, error,
            )
        else:
            logger.error(
                "%s Échec du vote (%s)",
                self.log_prefix, error,
            )
