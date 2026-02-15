import asyncio
import logging
import random
from datetime import datetime, timedelta

from browser import BrowserManager
from voters.base import BaseVoter

logger = logging.getLogger("auto-voter")


class VoteScheduler:
    """Gère les timers indépendants pour chaque site de vote."""

    def __init__(self, browser: BrowserManager, voters: list[BaseVoter]):
        self.browser = browser
        self.voters = voters
        self._tasks: list[asyncio.Task] = []

    async def start(self):
        """Lance les boucles de vote en parallèle pour chaque voter."""
        logger.info("Démarrage du scheduler avec %d site(s)", len(self.voters))
        self._tasks = [
            asyncio.create_task(self._vote_loop(voter))
            for voter in self.voters
        ]
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self):
        """Arrête toutes les boucles de vote."""
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _vote_loop(self, voter: BaseVoter):
        """Boucle de vote pour un site : vote immédiat puis intervalles."""
        # Vote immédiat au démarrage
        await self._execute_vote(voter)

        while True:
            delay = self._compute_delay(voter)
            next_time = datetime.now() + timedelta(seconds=delay)
            logger.info(
                "[%s] Prochain vote à %s (dans %dm%ds)",
                voter.name,
                next_time.strftime("%H:%M"),
                delay // 60,
                delay % 60,
            )
            await asyncio.sleep(delay)
            await self._execute_vote(voter)

    async def _execute_vote(self, voter: BaseVoter):
        """Exécute un vote sur un site, avec gestion des erreurs navigateur."""
        page = None
        try:
            if not self.browser.is_running:
                await self.browser.restart()

            page = await self.browser.new_page()
            success = await voter.vote(page)

            if success:
                next_time = datetime.now() + timedelta(
                    minutes=voter.interval_minutes,
                    seconds=random.randint(120, voter.random_delay_max * 60),
                )
                logger.info(
                    "[%s] Vote #%d réussi - Prochain: %s",
                    voter.name, voter.vote_count,
                    next_time.strftime("%H:%M"),
                )
            else:
                next_time = datetime.now() + timedelta(
                    minutes=voter.interval_minutes,
                    seconds=random.randint(120, voter.random_delay_max * 60),
                )
                logger.warning(
                    "[%s] Échec - Retry: %s",
                    voter.name,
                    next_time.strftime("%H:%M"),
                )

        except Exception as e:
            logger.error("[%s] Erreur inattendue: %s", voter.name, e)
            try:
                await self.browser.restart()
            except Exception as restart_err:
                logger.error("Impossible de redémarrer le navigateur: %s", restart_err)

        finally:
            if page and not page.is_closed():
                try:
                    await page.close()
                except Exception:
                    pass

    @staticmethod
    def _compute_delay(voter: BaseVoter) -> int:
        """Calcule le délai avant le prochain vote (intervalle + marge aléatoire)."""
        base = voter.interval_minutes * 60
        jitter = random.randint(2 * 60, voter.random_delay_max * 60)
        return base + jitter
