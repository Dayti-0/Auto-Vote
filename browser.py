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


def compute_proxy_timeouts(latency_ms: float) -> tuple[int, float]:
    """Calcule les timeouts adaptatifs basés sur la latence mesurée du proxy.

    La latence mesurée correspond à un aller-retour complet (connexion + fetch).
    Une page réelle nécessite plusieurs aller-retours (HTML, CSS, JS, etc.)
    donc on multiplie par un facteur pour couvrir le chargement complet.

    Returns:
        (navigation_timeout_ms, timeout_factor)
    """
    if latency_ms <= 0:
        # Pas de mesure disponible : valeurs par défaut pour proxy
        return 30_000, 2.0
    # Navigation timeout : latence × 6 (couvre multi-requêtes + parsing)
    # Min 30s, max 120s
    nav_timeout = min(120_000, max(30_000, int(latency_ms * 6)))
    # Facteur multiplicateur pour les attentes d'éléments (waits internes)
    # Min 2.0, max 5.0
    factor = min(5.0, max(2.0, latency_ms / 3000))
    return nav_timeout, factor


class BrowserManager:
    """Gère une instance unique du navigateur Playwright avec plusieurs contextes (un par compte)."""

    def __init__(self, headless: bool = True, slow_mo: int = 0):
        self.headless = headless
        self.slow_mo = slow_mo
        self._playwright = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}  # pseudo -> context
        self._proxy_pseudos: set[str] = set()  # pseudos utilisant un proxy
        self._proxy_latencies: dict[str, float] = {}  # pseudo -> latency_ms

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
        logger.debug("Navigateur lancé avec succès")

    async def create_context(self, pseudo: str, proxy: str | None = None,
                             latency_ms: float = 0) -> BrowserContext:
        """Crée un contexte navigateur isolé pour un compte, avec proxy optionnel.

        Args:
            latency_ms: Latence mesurée du proxy en ms. Utilisée pour calculer
                        des timeouts adaptatifs (plus le proxy est lent, plus on attend).
        """
        if not self._browser:
            await self.start()

        context_kwargs = {
            "user_agent": USER_AGENT,
            "viewport": {"width": 1920, "height": 1080},
            "locale": "fr-FR",
        }

        if proxy:
            context_kwargs["proxy"] = _parse_proxy(proxy)
            self._proxy_pseudos.add(pseudo)
            if latency_ms > 0:
                self._proxy_latencies[pseudo] = latency_ms
            nav_timeout, _ = compute_proxy_timeouts(
                self._proxy_latencies.get(pseudo, 0)
            )
            logger.info(
                "[%s] Contexte créé avec proxy: %s (timeout adapté: %ds pour ~%.0fms de latence)",
                pseudo, _mask_proxy(proxy), nav_timeout // 1000,
                self._proxy_latencies.get(pseudo, 0),
            )
        else:
            self._proxy_pseudos.discard(pseudo)
            self._proxy_latencies.pop(pseudo, None)
            nav_timeout = 15000
            logger.info("[%s] Contexte créé sans proxy (IP locale)", pseudo)

        context = await self._browser.new_context(**context_kwargs)
        context.set_default_timeout(nav_timeout)
        context.set_default_navigation_timeout(nav_timeout)

        self._contexts[pseudo] = context
        return context

    async def new_page(self, pseudo: str) -> Page:
        """Ouvre un nouvel onglet dans le contexte du compte donné."""
        context = self._contexts.get(pseudo)
        if not context:
            raise RuntimeError(f"Aucun contexte navigateur pour le pseudo '{pseudo}'")
        return await context.new_page()

    async def check_ip(self, pseudo: str) -> str | None:
        """Vérifie l'IP visible du contexte d'un compte via ipify.

        Retourne l'IP en texte ou None en cas d'erreur.
        """
        page = None
        try:
            page = await self.new_page(pseudo)
            if pseudo in self._proxy_pseudos:
                ip_timeout, _ = compute_proxy_timeouts(
                    self._proxy_latencies.get(pseudo, 0)
                )
            else:
                ip_timeout = 15000
            await page.goto(
                "https://api.ipify.org",
                wait_until="domcontentloaded",
                timeout=ip_timeout,
            )
            ip_text = (await page.inner_text("body")).strip()
            return ip_text
        except Exception as e:
            logger.debug("[%s] Impossible de vérifier l'IP: %s", pseudo, e)
            return None
        finally:
            if page and not page.is_closed():
                try:
                    await page.close()
                except Exception:
                    pass

    async def close(self):
        """Ferme le navigateur proprement."""
        logger.info("Fermeture du navigateur")
        for pseudo, context in self._contexts.items():
            try:
                await context.close()
            except Exception:
                pass
        self._contexts.clear()
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def restart(self):
        """Redémarre le navigateur en cas de crash."""
        logger.warning("Redémarrage du navigateur...")
        # Sauvegarder les infos des contextes pour les recréer
        old_contexts = dict(self._contexts)
        await self.close()
        await self.start()
        # Note : les contextes doivent être recréés par l'appelant

    async def restart_context(self, pseudo: str, proxy: str | None = None,
                              latency_ms: float = 0):
        """Recrée le contexte d'un compte après un crash."""
        old_ctx = self._contexts.pop(pseudo, None)
        if old_ctx:
            try:
                await old_ctx.close()
            except Exception:
                pass
        await self.create_context(pseudo, proxy, latency_ms=latency_ms)

    @property
    def is_running(self) -> bool:
        return self._browser is not None and self._browser.is_connected()


def _parse_proxy(proxy_str: str) -> dict:
    """Parse une chaîne proxy en dict Playwright.

    Formats supportés :
      - http://ip:port
      - http://user:pass@ip:port
      - socks5://ip:port
      - socks5://user:pass@ip:port
    """
    result = {"server": proxy_str}

    # Extraire user:pass si présent
    # Format: scheme://user:pass@host:port
    if "@" in proxy_str:
        scheme_rest = proxy_str.split("://", 1)
        if len(scheme_rest) == 2:
            scheme = scheme_rest[0]
            rest = scheme_rest[1]
            auth_host = rest.split("@", 1)
            if len(auth_host) == 2:
                auth = auth_host[0]
                host = auth_host[1]
                result["server"] = f"{scheme}://{host}"
                user_pass = auth.split(":", 1)
                result["username"] = user_pass[0]
                if len(user_pass) == 2:
                    result["password"] = user_pass[1]

    return result


def _mask_proxy(proxy_str: str) -> str:
    """Masque le mot de passe dans une chaîne proxy pour les logs."""
    if "@" not in proxy_str:
        return proxy_str
    scheme_rest = proxy_str.split("://", 1)
    if len(scheme_rest) != 2:
        return proxy_str
    scheme = scheme_rest[0]
    rest = scheme_rest[1]
    auth_host = rest.split("@", 1)
    if len(auth_host) != 2:
        return proxy_str
    auth = auth_host[0]
    host = auth_host[1]
    user_pass = auth.split(":", 1)
    user = user_pass[0]
    return f"{scheme}://{user}:***@{host}"


async def human_delay(min_sec: float = 1.0, max_sec: float = 3.0):
    """Attend un délai aléatoire pour simuler un comportement humain."""
    import asyncio
    delay = random.uniform(min_sec, max_sec)
    logger.debug("Délai humain: %.1fs", delay)
    await asyncio.sleep(delay)


