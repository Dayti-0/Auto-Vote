import asyncio
import signal
import sys
import os

import yaml

from browser import BrowserManager
from logger_setup import setup_logger
from scheduler import VoteScheduler
from voters import (
    ServeurMinecraftVoteVoter,
    ServeurPriveVoter,
    ServeurMinecraftVoter,
)


def load_config(path: str = "config.yaml") -> dict:
    """Charge la configuration depuis le fichier YAML."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def print_banner(pseudo: str, headless: bool, sites_config: dict):
    """Affiche la bannière de démarrage."""
    mode = "headless" if headless else "visible"
    smv = sites_config.get("serveur_minecraft_vote", {})
    sp = sites_config.get("serveur_prive", {})
    sm = sites_config.get("serveur_minecraft", {})

    lines = [
        "",
        "══════════════════════════════════════════",
        "     Minecraft Auto-Voter v1.0",
        "     Serveur: SurvivalWorld",
        f"     Pseudo: {pseudo}",
        "══════════════════════════════════════════",
        "  Sites actifs:",
    ]

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


def build_voters(pseudo: str, sites_config: dict) -> list:
    """Crée les instances de voters selon la configuration."""
    voters = []

    smv = sites_config.get("serveur_minecraft_vote", {})
    if smv.get("enabled", True):
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

    pseudo = config.get("pseudo", "CHANGE_ME")
    if pseudo == "CHANGE_ME":
        print("ERREUR: Veuillez configurer votre pseudo dans config.yaml")
        sys.exit(1)

    headless = config.get("headless", True)
    slow_mo = config.get("slow_mo", 0)
    sites_config = config.get("sites", {})

    # Setup logging
    logger = setup_logger(
        log_level=config.get("log_level", "INFO"),
        log_file=config.get("log_file", "logs/votes.log"),
    )

    # Bannière
    print_banner(pseudo, headless, sites_config)

    # Créer les voters
    voters = build_voters(pseudo, sites_config)
    if not voters:
        logger.error("Aucun site de vote actif ! Vérifiez config.yaml")
        sys.exit(1)

    logger.info("Démarrage avec %d site(s) actif(s) pour le pseudo '%s'", len(voters), pseudo)

    # Navigateur
    browser = BrowserManager(headless=headless, slow_mo=slow_mo)
    await browser.start()

    # Scheduler
    scheduler = VoteScheduler(browser, voters)

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
