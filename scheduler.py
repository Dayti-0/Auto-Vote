import asyncio
import logging
import random
from datetime import datetime, timedelta

from browser import BrowserManager
from voters.base import BaseVoter

logger = logging.getLogger("auto-voter")


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
            # Pour les comptes auto-proxy : nouveau proxy frais avant chaque vote
            if account.is_auto_proxy:
                await self._rotate_proxy(account)

            page = None
            try:
                if not self.browser.is_running:
                    await self.browser.restart()
                    # Recréer tous les contextes après un restart du navigateur
                    for ag in self.account_groups:
                        await self.browser.create_context(ag.pseudo, ag.proxy)

                page = await self.browser.new_page(account.pseudo)
                success = await voter.vote(page)

                if not success:
                    logger.warning("[%s][%s] Vote échoué", account.pseudo, voter.name)

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

    async def _rotate_proxy(self, account: AccountVoters):
        """Récupère un nouveau proxy frais et recrée le contexte navigateur."""
        try:
            from proxy_manager import find_working_proxies
            working = await find_working_proxies(count=1)
            if working:
                new_proxy = working[0]["url"]
                latency = working[0]["latency_ms"]
                old_proxy = account.proxy
                account.proxy = new_proxy
                await self.browser.restart_context(account.pseudo, new_proxy)
                if new_proxy != old_proxy:
                    logger.info(
                        "[%s] Nouveau proxy: %s (latence: %.0fms)",
                        account.pseudo, new_proxy, latency,
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
