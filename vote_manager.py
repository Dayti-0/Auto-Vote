"""Bridge entre Flask et le systeme de vote async.

Gere le cycle de vie du voting (start/stop) dans un thread en arriere-plan
et expose l'etat en temps reel pour l'interface web.
"""

import asyncio
import logging
import os
import signal
import threading
from datetime import datetime

import yaml

from browser import BrowserManager
from logger_setup import setup_logger
from proxy_manager import assign_auto_proxies, get_local_ip
from scheduler import AccountVoters, VoteScheduler
from voters import (
    ServeurMinecraftVoteVoter,
    ServeurMinecraftVoter,
    ServeurPriveVoter,
)

logger = logging.getLogger("auto-voter")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(config: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def build_voters(pseudo: str, sites_config: dict, proxy: str | None = None) -> list:
    voters = []
    smv = sites_config.get("serveur_minecraft_vote", {})
    if smv.get("enabled", True):
        if proxy:
            logger.info(
                "[%s] serveur-minecraft-vote.fr ignoré (incompatible avec proxy)", pseudo,
            )
        else:
            voters.append(ServeurMinecraftVoteVoter(
                pseudo=pseudo,
                interval_minutes=smv.get("interval_minutes", 90),
                random_delay_max=smv.get("random_delay_max", 5),
            ))
    sp = sites_config.get("serveur_prive", {})
    if sp.get("enabled", True):
        voters.append(ServeurPriveVoter(
            pseudo=pseudo,
            interval_minutes=sp.get("interval_minutes", 90),
            random_delay_max=sp.get("random_delay_max", 5),
        ))
    sm = sites_config.get("serveur_minecraft", {})
    if sm.get("enabled", True):
        voters.append(ServeurMinecraftVoter(
            pseudo=pseudo,
            interval_minutes=sm.get("interval_minutes", 180),
            random_delay_max=sm.get("random_delay_max", 5),
        ))
    return voters


class VoteManager:
    """Gere le processus de vote en arriere-plan, expose l'etat pour Flask."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._scheduler: VoteScheduler | None = None
        self._browser: BrowserManager | None = None
        self._running = False
        self._account_groups: list[AccountVoters] = []
        self._start_time: datetime | None = None
        self._stop_event: asyncio.Event | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def start_time(self) -> datetime | None:
        return self._start_time

    @property
    def account_groups(self) -> list[AccountVoters]:
        return self._account_groups

    def get_status(self) -> dict:
        """Retourne l'etat complet pour le dashboard."""
        accounts_status = []
        for ag in self._account_groups:
            for voter in ag.voters:
                accounts_status.append({
                    "pseudo": ag.pseudo,
                    "proxy": ag.proxy,
                    "is_auto_proxy": ag.is_auto_proxy,
                    "site": voter.name,
                    "vote_count": voter.vote_count,
                    "consecutive_failures": voter.consecutive_failures,
                    "last_vote_time": voter.last_vote_time.strftime("%H:%M:%S") if voter.last_vote_time else None,
                    "last_error": voter.last_error,
                    "next_vote_available": voter.next_vote_available.strftime("%H:%M") if voter.next_vote_available else None,
                })

        total_votes = sum(v.vote_count for ag in self._account_groups for v in ag.voters)
        total_failures = sum(v.consecutive_failures for ag in self._account_groups for v in ag.voters)

        return {
            "running": self._running,
            "start_time": self._start_time.strftime("%Y-%m-%d %H:%M:%S") if self._start_time else None,
            "total_votes": total_votes,
            "total_failures": total_failures,
            "accounts": accounts_status,
            "num_accounts": len(self._account_groups),
        }

    def start(self):
        """Demarre le voting en arriere-plan."""
        if self._running:
            return

        self._thread = threading.Thread(target=self._run_voting_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Arrete le voting."""
        if not self._running or not self._loop:
            return

        # Signal l'arret dans la boucle async
        if self._stop_event and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop_event.set)

        # Attendre la fin du thread
        if self._thread:
            self._thread.join(timeout=15)
        self._running = False
        self._account_groups = []
        self._start_time = None

    def _run_voting_loop(self):
        """Execute la boucle de vote dans un thread dedie."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_voting())
        except Exception as e:
            logger.error("Erreur dans le thread de voting: %s", e)
        finally:
            self._running = False
            self._loop.close()
            self._loop = None

    async def _async_voting(self):
        """Coeur async du voting."""
        config = load_config()
        accounts = self._parse_accounts(config)

        # Valider les pseudos
        accounts = [acc for acc in accounts if acc.get("pseudo", "CHANGE_ME") != "CHANGE_ME"]
        if not accounts:
            logger.warning("Aucun compte valide configure")
            return

        headless = config.get("headless", True)
        slow_mo = config.get("slow_mo", 0)
        sites_config = config.get("sites", {})

        # Setup logging
        setup_logger(
            log_level=config.get("log_level", "INFO"),
            log_file=config.get("log_file", "logs/votes.log"),
        )

        # Detecter l'IP locale
        local_ip = await get_local_ip()

        # Resoudre les proxies auto
        has_auto = any(acc.get("proxy") == "auto" for acc in accounts)
        if has_auto:
            logger.info("Recherche de proxies automatiques (ProxyScrape)...")
            accounts = await assign_auto_proxies(accounts)

        # Construire les groupes
        account_groups = []
        for acc in accounts:
            pseudo = acc["pseudo"]
            proxy = acc.get("proxy")
            is_auto = bool(acc.get("_proxy_auto"))
            voters = build_voters(pseudo, sites_config, proxy=proxy)
            if proxy:
                for v in voters:
                    v.timeout_factor = 2.0
            if voters:
                account_groups.append(AccountVoters(
                    pseudo=pseudo, proxy=proxy, voters=voters, is_auto_proxy=is_auto,
                ))

        if not account_groups:
            logger.warning("Aucun site de vote actif")
            return

        self._account_groups = account_groups
        self._running = True
        self._start_time = datetime.now()
        self._stop_event = asyncio.Event()

        # Navigateur
        self._browser = BrowserManager(headless=headless, slow_mo=slow_mo)
        await self._browser.start()

        for ag in account_groups:
            await self._browser.create_context(ag.pseudo, ag.proxy)

        # Scheduler
        self._scheduler = VoteScheduler(self._browser, account_groups)

        logger.info(
            "Voting demarre: %d compte(s), %d tache(s)",
            len(account_groups),
            sum(len(ag.voters) for ag in account_groups),
        )

        # Lancer le scheduler et attendre le signal d'arret
        scheduler_task = asyncio.create_task(self._scheduler.start())

        await self._stop_event.wait()

        # Nettoyage
        logger.info("Arret du voting...")
        await self._scheduler.stop()
        await self._browser.close()
        self._browser = None
        self._scheduler = None
        logger.info("Voting arrete proprement")

    @staticmethod
    def _parse_accounts(config: dict) -> list[dict]:
        if "accounts" in config:
            accounts = config["accounts"]
            if accounts and isinstance(accounts, list):
                return accounts
        pseudo = config.get("pseudo", "CHANGE_ME")
        if pseudo != "CHANGE_ME":
            return [{"pseudo": pseudo, "proxy": None}]
        return []
