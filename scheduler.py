import asyncio
import logging
import random
from datetime import datetime, timedelta

from browser import BrowserManager
from voters.base import BaseVoter

logger = logging.getLogger("auto-voter")

# Mots-clés dans les messages d'erreur qui indiquent un problème de proxy
_PROXY_ERROR_KEYWORDS = [
    "ERR_CONNECTION_RESET",
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_CONNECTION_FAILED",
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_SOCKS_CONNECTION_FAILED",
    "ERR_PROXY_AUTH",
    "ERR_EMPTY_RESPONSE",
]


def _is_proxy_error(error: str) -> bool:
    """Détecte si une erreur est liée au proxy (connexion réseau)."""
    error_upper = error.upper()
    return any(kw in error_upper for kw in _PROXY_ERROR_KEYWORDS)


class AccountVoters:
    """Regroupe les voters d'un même compte avec leur proxy."""

    def __init__(self, pseudo: str, proxy: str | None, voters: list[BaseVoter],
                 is_auto_proxy: bool = False):
        self.pseudo = pseudo
        self.proxy = proxy
        self.voters = voters
        self.is_auto_proxy = is_auto_proxy


class VoteScheduler:
    """Gère les timers indépendants pour chaque site de vote, par compte."""

    def __init__(self, browser: BrowserManager, account_groups: list[AccountVoters]):
        self.browser = browser
        self.account_groups = account_groups
        self._tasks: list[asyncio.Task] = []
        # Un lock par pseudo pour éviter les conflits de session sur survivalworld.fr
        # Les comptes différents peuvent voter en parallèle
        self._vote_locks: dict[str, asyncio.Lock] = {
            ag.pseudo: asyncio.Lock() for ag in account_groups
        }
        # Comptes dont l'IP proxy a déjà été vérifiée (évite de vérifier 3x)
        self._ip_verified: set[str] = set()

    async def start(self):
        """Lance les boucles de vote en parallèle pour chaque voter de chaque compte."""
        total = sum(len(ag.voters) for ag in self.account_groups)
        logger.info(
            "Démarrage du scheduler avec %d compte(s) et %d tâche(s) de vote",
            len(self.account_groups), total,
        )
        for ag in self.account_groups:
            for voter in ag.voters:
                self._tasks.append(
                    asyncio.create_task(self._vote_loop(ag, voter))
                )
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self):
        """Arrête toutes les boucles de vote."""
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _vote_loop(self, account: AccountVoters, voter: BaseVoter):
        """Boucle de vote pour un site d'un compte : vote immédiat puis intervalles."""
        # Vérifier l'IP du proxy avant le premier vote (une seule fois par compte)
        if account.proxy and account.pseudo not in self._ip_verified:
            self._ip_verified.add(account.pseudo)
            await self._verify_proxy_ip(account)

        # Vote immédiat au démarrage
        await self._execute_vote(account, voter)

        while True:
            delay = self._compute_smart_delay(voter)
            next_time = datetime.now() + timedelta(seconds=delay)
            logger.info(
                "[%s][%s] Prochain vote à %s (dans %dm%ds)",
                account.pseudo,
                voter.name,
                next_time.strftime("%H:%M"),
                delay // 60,
                delay % 60,
            )
            await asyncio.sleep(delay)
            await self._execute_vote(account, voter)

    async def _execute_vote(self, account: AccountVoters, voter: BaseVoter):
        """Exécute un vote sur un site, avec gestion des erreurs navigateur."""
        lock = self._vote_locks[account.pseudo]
        async with lock:
            page = None
            try:
                if not self.browser.is_running:
                    await self.browser.restart()
                    # Recréer tous les contextes après un restart du navigateur
                    for ag in self.account_groups:
                        await self.browser.create_context(ag.pseudo, ag.proxy)

                page = await self.browser.new_page(account.pseudo)
                success = await voter.vote(page)

                if success:
                    pass  # Succès déjà loggé par le voter
                elif voter.next_vote_available:
                    # Cooldown détecté — comportement normal, pas d'erreur
                    pass
                else:
                    logger.warning("[%s][%s] Vote échoué", account.pseudo, voter.name)
                    # Si auto-proxy et erreur de connexion proxy : rotation + retry
                    if (account.is_auto_proxy and voter.last_error
                            and _is_proxy_error(voter.last_error)):
                        logger.info(
                            "[%s][%s] Erreur proxy détectée, rotation et retry...",
                            account.pseudo, voter.name,
                        )
                        if page and not page.is_closed():
                            await page.close()
                            page = None
                        await self._rotate_proxy(account)
                        try:
                            page = await self.browser.new_page(account.pseudo)
                            success = await voter.vote(page)
                            if not success and not voter.next_vote_available:
                                logger.warning(
                                    "[%s][%s] Vote échoué après rotation proxy",
                                    account.pseudo, voter.name,
                                )
                        except Exception as retry_err:
                            logger.error(
                                "[%s][%s] Échec retry après rotation: %s",
                                account.pseudo, voter.name, retry_err,
                            )

            except Exception as e:
                logger.error("[%s][%s] Erreur inattendue: %s", account.pseudo, voter.name, e)
                try:
                    await self.browser.restart_context(account.pseudo, account.proxy)
                except Exception as restart_err:
                    logger.error(
                        "[%s] Impossible de recréer le contexte: %s",
                        account.pseudo, restart_err,
                    )

            finally:
                if page and not page.is_closed():
                    try:
                        await page.close()
                    except Exception:
                        pass

    async def _verify_proxy_ip(self, account: AccountVoters):
        """Vérifie que le proxy donne bien une IP différente de l'IP locale.

        Si l'IP est identique (proxy transparent), tente une rotation.
        Exécuté une seule fois par compte au démarrage de sa première boucle.
        """
        lock = self._vote_locks[account.pseudo]
        async with lock:
            try:
                from proxy_manager import get_local_ip

                local_ip = await get_local_ip()
                proxy_ip = await self.browser.check_ip(account.pseudo)

                if not proxy_ip:
                    logger.warning(
                        "[%s] Impossible de vérifier l'IP du proxy, rotation...",
                        account.pseudo,
                    )
                    await self._rotate_proxy(account)
                    return

                if local_ip and proxy_ip == local_ip:
                    logger.warning(
                        "[%s] Proxy transparent détecté ! IP proxy = IP locale (%s), rotation...",
                        account.pseudo, proxy_ip,
                    )
                    await self._rotate_proxy(account)
                    # Re-vérifier après rotation
                    new_ip = await self.browser.check_ip(account.pseudo)
                    if new_ip and new_ip != local_ip:
                        logger.info(
                            "[%s] Nouvelle IP proxy vérifiée: %s (locale: %s)",
                            account.pseudo, new_ip, local_ip,
                        )
                    elif new_ip:
                        logger.error(
                            "[%s] Proxy toujours transparent après rotation (IP: %s)",
                            account.pseudo, new_ip,
                        )
                else:
                    logger.info(
                        "[%s] IP proxy vérifiée: %s (locale: %s)",
                        account.pseudo, proxy_ip, local_ip or "inconnue",
                    )
            except Exception as e:
                logger.warning("[%s] Erreur vérification IP: %s", account.pseudo, e)

    async def _rotate_proxy(self, account: AccountVoters):
        """Récupère un nouveau proxy frais et recrée le contexte navigateur."""
        try:
            from proxy_manager import find_working_proxies, get_local_ip

            local_ip = await get_local_ip()

            # Collecter les IPs des autres comptes pour éviter les doublons
            exclude_ips = []
            if local_ip:
                exclude_ips.append(local_ip)

            working = await find_working_proxies(
                count=1,
                local_ip=local_ip,
                exclude_ips=exclude_ips,
            )
            if working:
                new_proxy = working[0]["url"]
                latency = working[0]["latency_ms"]
                new_ip = working[0].get("ip", "?")
                old_proxy = account.proxy
                account.proxy = new_proxy
                await self.browser.restart_context(account.pseudo, new_proxy)
                if new_proxy != old_proxy:
                    logger.info(
                        "[%s] Nouveau proxy: %s (IP: %s, latence: %.0fms)",
                        account.pseudo, new_proxy, new_ip, latency,
                    )
                else:
                    logger.debug("[%s] Proxy inchangé: %s", account.pseudo, new_proxy)
            else:
                logger.warning(
                    "[%s] Aucun proxy disponible, vote avec le proxy actuel (%s)",
                    account.pseudo, account.proxy or "IP locale",
                )
        except Exception as e:
            logger.error("[%s] Erreur rotation proxy: %s — on garde l'actuel", account.pseudo, e)

    @staticmethod
    def _compute_smart_delay(voter: BaseVoter) -> int:
        """Calcule le délai en tenant compte du cooldown détecté sur le site."""
        if voter.next_vote_available:
            remaining = (voter.next_vote_available - datetime.now()).total_seconds()
            voter.next_vote_available = None  # Reset après utilisation
            if remaining > 0:
                # Ajouter 1-3 min de marge après l'expiration du cooldown
                jitter = random.randint(60, 180)
                return int(remaining + jitter)
        return VoteScheduler._compute_delay(voter)

    @staticmethod
    def _compute_delay(voter: BaseVoter) -> int:
        """Calcule le délai avant le prochain vote (intervalle + marge aléatoire)."""
        base = voter.interval_minutes * 60
        max_jitter = voter.random_delay_max * 60
        if max_jitter <= 0:
            return base
        jitter = random.randint(0, max_jitter)
        return base + jitter
