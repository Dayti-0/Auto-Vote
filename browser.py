import logging
import os
import random

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger("auto-voter")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def _find_chromium_executable() -> str | None:
    """Cherche l'exécutable Chromium dans le cache Playwright."""
    cache_dir = os.path.expanduser("~/.cache/ms-playwright")
    if not os.path.isdir(cache_dir):
        return None
    # Chercher les dossiers chromium-* triés par version décroissante
    candidates = sorted(
        [d for d in os.listdir(cache_dir) if d.startswith("chromium-")],
        reverse=True,
    )
    for candidate in candidates:
        exe = os.path.join(cache_dir, candidate, "chrome-linux", "chrome")
        if os.path.isfile(exe):
            return exe
    return None


class BrowserManager:
    """Gère une instance unique du navigateur Playwright."""

    def __init__(self, headless: bool = True, slow_mo: int = 0):
        self.headless = headless
        self.slow_mo = slow_mo
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def start(self):
        """Lance le navigateur Chromium."""
        logger.info("Lancement du navigateur Chromium (headless=%s)", self.headless)
        self._playwright = await async_playwright().start()

        launch_kwargs = {
            "headless": self.headless,
            "slow_mo": self.slow_mo,
        }
        # Utiliser un exécutable Chromium déjà installé si la version par défaut est absente
        exe = _find_chromium_executable()
        if exe:
            launch_kwargs["executable_path"] = exe
            logger.debug("Utilisation de Chromium: %s", exe)

        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        self._context = await self._browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="fr-FR",
        )
        self._context.set_default_timeout(30000)  # 30 secondes
        self._context.set_default_navigation_timeout(30000)
        logger.debug("Navigateur lancé avec succès")

    async def new_page(self) -> Page:
        """Ouvre un nouvel onglet dans le contexte existant."""
        if not self._context:
            await self.start()
        return await self._context.new_page()

    async def close(self):
        """Ferme le navigateur proprement."""
        logger.info("Fermeture du navigateur")
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def restart(self):
        """Redémarre le navigateur en cas de crash."""
        logger.warning("Redémarrage du navigateur...")
        await self.close()
        await self.start()

    @property
    def is_running(self) -> bool:
        return self._browser is not None and self._browser.is_connected()


async def human_delay(min_sec: float = 1.0, max_sec: float = 3.0):
    """Attend un délai aléatoire pour simuler un comportement humain."""
    import asyncio
    delay = random.uniform(min_sec, max_sec)
    logger.debug("Délai humain: %.1fs", delay)
    await asyncio.sleep(delay)
