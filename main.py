import asyncio
import signal
import sys
import os

import yaml

from browser import BrowserManager
from logger_setup import setup_logger
from proxy_manager import assign_auto_proxies
from scheduler import VoteScheduler, AccountVoters
from voters import (
    ServeurMinecraftVoteVoter,
    ServeurPriveVoter,
    ServeurMinecraftVoter,
)


def load_config(path: str = "config.yaml") -> dict:
    """Charge la configuration depuis le fichier YAML."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_accounts(config: dict) -> list[dict]:
    """Parse les comptes depuis la config (supporte l'ancien et le nouveau format)."""
    # Nouveau format : liste accounts
    if "accounts" in config:
        accounts = config["accounts"]
        if not accounts or not isinstance(accounts, list):
            print("ERREUR: 'accounts' doit être une liste non vide dans config.yaml")
            sys.exit(1)
        return accounts

    # Ancien format : pseudo unique (rétrocompatibilité)
    pseudo = config.get("pseudo", "CHANGE_ME")
    if pseudo == "CHANGE_ME":
        print("ERREUR: Veuillez configurer votre pseudo dans config.yaml")
        sys.exit(1)
    return [{"pseudo": pseudo, "proxy": None}]


def print_banner(accounts: list[dict], headless: bool, sites_config: dict,
                 local_ip: str | None = None):
    """Affiche la bannière de démarrage."""
    mode = "headless" if headless else "visible"
    smv = sites_config.get("serveur_minecraft_vote", {})
    sp = sites_config.get("serveur_prive", {})
    sm = sites_config.get("serveur_minecraft", {})

    lines = [
        "",
        "══════════════════════════════════════════",
        "     Minecraft Auto-Voter v1.1",
        "     Serveur: SurvivalWorld",
        f"     Comptes: {len(accounts)}",
        "══════════════════════════════════════════",
    ]

    # Afficher les comptes
    lines.append("  Comptes actifs:")
    for acc in accounts:
        pseudo = acc["pseudo"]
        proxy = acc.get("proxy")
        if proxy == "auto":
            lines.append(f"  [OK] {pseudo} (proxy: auto - ProxyScrape)")
        elif proxy:
            from browser import _mask_proxy
            proxy_display = _mask_proxy(proxy)
            latency = acc.get("_proxy_latency")
            proxy_ip = acc.get("_proxy_ip")
            parts = [f"proxy: {proxy_display}"]
            if proxy_ip:
                parts.append(f"IP: {proxy_ip}")
            if latency:
                parts.append(f"~{latency:.0f}ms")
            lines.append(f"  [OK] {pseudo} ({', '.join(parts)})")
        else:
            if local_ip:
                lines.append(f"  [OK] {pseudo} (IP locale: {local_ip})")
            else:
                lines.append(f"  [OK] {pseudo} (IP locale)")

    lines.append("══════════════════════════════════════════")
    lines.append("  Sites actifs:")

    if smv.get("enabled", True):
        lines.append(f"  [OK] serveur-minecraft-vote.fr ({smv.get('interval_minutes', 90)}min)")
    else:
        lines.append("  [--] serveur-minecraft-vote.fr (desactive)")

    if sp.get("enabled", True):
        lines.append(f"  [OK] serveur-prive.net ({sp.get('interval_minutes', 90)}min)")
    else:
        lines.append("  [--] serveur-prive.net (desactive)")

    if sm.get("enabled", True):
        lines.append(f"  [OK] serveur-minecraft.com ({sm.get('interval_minutes', 180)}min)")
    else:
        lines.append("  [--] serveur-minecraft.com (desactive)")

    lines.append("══════════════════════════════════════════")
    lines.append(f"  Mode: {mode}")
    lines.append("  Votes lances...")
    lines.append("══════════════════════════════════════════")
    lines.append("")

    print("\n".join(lines))


def build_voters(pseudo: str, sites_config: dict, proxy: str | None = None) -> list:
    """Crée les instances de voters pour un pseudo selon la configuration.

    Args:
        proxy: Si défini, serveur-minecraft-vote.fr est ignoré (incompatible avec les proxies).
    """
    voters = []

    smv = sites_config.get("serveur_minecraft_vote", {})
    if smv.get("enabled", True):
        if proxy:
            import logging
            logging.getLogger("auto-voter").info(
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


async def main():
    # Se placer dans le répertoire du script
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # Charger la config
    config = load_config()

    # Parser les comptes
    accounts = parse_accounts(config)

    # Valider les pseudos
    for acc in accounts:
        pseudo = acc.get("pseudo", "CHANGE_ME")
        if pseudo == "CHANGE_ME":
            print("ERREUR: Un des comptes a encore le pseudo 'CHANGE_ME' dans config.yaml")
            sys.exit(1)

    headless = config.get("headless", True)
    slow_mo = config.get("slow_mo", 0)
    sites_config = config.get("sites", {})

    # Setup logging
    logger = setup_logger(
        log_level=config.get("log_level", "INFO"),
        log_file=config.get("log_file", "logs/votes.log"),
    )

    # Détecter l'IP locale (utile pour rejeter les proxies transparents)
    from proxy_manager import get_local_ip
    local_ip = await get_local_ip()

    # Résoudre les proxies automatiques (proxy: "auto")
    has_auto = any(acc.get("proxy") == "auto" for acc in accounts)
    if has_auto:
        print("Recherche de proxies automatiques (ProxyScrape)...")
        accounts = await assign_auto_proxies(accounts)

    # Bannière (après résolution des proxies pour afficher les IPs réelles)
    print_banner(accounts, headless, sites_config, local_ip=local_ip)

    # Construire les groupes de voters par compte
    account_groups = []
    for acc in accounts:
        pseudo = acc["pseudo"]
        proxy = acc.get("proxy")
        is_auto = bool(acc.get("_proxy_auto"))
        voters = build_voters(pseudo, sites_config, proxy=proxy)
        # Timeouts plus longs pour les comptes via proxy (latence réseau)
        if proxy:
            for v in voters:
                v.timeout_factor = 2.0
        if voters:
            account_groups.append(AccountVoters(
                pseudo=pseudo, proxy=proxy, voters=voters, is_auto_proxy=is_auto,
            ))

    if not account_groups:
        logger.error("Aucun site de vote actif ! Vérifiez config.yaml")
        sys.exit(1)

    total_voters = sum(len(ag.voters) for ag in account_groups)
    logger.info(
        "Démarrage avec %d compte(s) et %d tâche(s) de vote",
        len(account_groups), total_voters,
    )

    # Navigateur
    browser = BrowserManager(headless=headless, slow_mo=slow_mo)
    await browser.start()

    # Créer un contexte isolé par compte (avec proxy si configuré)
    for ag in account_groups:
        await browser.create_context(ag.pseudo, ag.proxy)

    # Scheduler
    scheduler = VoteScheduler(browser, account_groups)

    # Gestion CTRL+C (cross-platform)
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def handle_signal():
        logger.info("Arrêt demandé (CTRL+C)...")
        shutdown_event.set()

    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_signal)
    else:
        # Windows: add_signal_handler is not supported, use signal.signal instead
        def _win_handler(signum, frame):
            loop.call_soon_threadsafe(shutdown_event.set)
        signal.signal(signal.SIGINT, _win_handler)
        signal.signal(signal.SIGTERM, _win_handler)

    # Lancer le scheduler en parallèle avec l'attente du signal
    scheduler_task = asyncio.create_task(scheduler.start())

    await shutdown_event.wait()

    # Nettoyage
    logger.info("Nettoyage en cours...")
    await scheduler.stop()
    await browser.close()
    logger.info("Programme terminé proprement.")


if __name__ == "__main__":
    asyncio.run(main())
