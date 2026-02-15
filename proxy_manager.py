import asyncio
import logging
import time

import aiohttp

logger = logging.getLogger("auto-voter")

# API ProxyScrape — proxies gratuits (HTTP/SOCKS4/SOCKS5)
# Pas de filtre country pour maximiser le nombre de proxies disponibles
PROXYSCRAPE_API = (
    "https://api.proxyscrape.com/v4/free-proxy-list/get"
    "?request=display_proxies"
    "&proxy_format=protocolipport"
    "&format=text"
    "&timeout=20000"
)

# URL pour vérifier l'IP visible du proxy (retourne l'IP en texte brut)
IP_CHECK_URL = "https://api.ipify.org"
# URL cible pour vérifier que le proxy peut réellement atteindre survivalworld.fr
TARGET_TEST_URL = "https://survivalworld.fr/vote"
# Timeout pour le test de chaque proxy (secondes)
TEST_TIMEOUT = 10
# Nombre max de proxies à tester en parallèle
MAX_CONCURRENT_TESTS = 20

# Cache de l'IP locale (détectée une seule fois)
_local_ip: str | None = None


async def get_local_ip() -> str | None:
    """Détecte l'IP publique locale (sans proxy)."""
    global _local_ip
    if _local_ip is not None:
        return _local_ip
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                IP_CHECK_URL,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    _local_ip = (await resp.text()).strip()
                    logger.info("IP locale détectée: %s", _local_ip)
                    return _local_ip
    except Exception as e:
        logger.warning("Impossible de détecter l'IP locale: %s", e)
    return None


async def fetch_proxy_list(api_url: str = PROXYSCRAPE_API) -> list[str]:
    """Récupère la liste de proxies depuis l'API ProxyScrape.

    Retourne une liste de chaînes au format 'protocol://ip:port'.
    """
    logger.info("Récupération des proxies depuis ProxyScrape...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.error("API ProxyScrape a répondu %d", resp.status)
                    return []
                text = await resp.text()
    except Exception as e:
        logger.error("Impossible de contacter l'API ProxyScrape: %s", e)
        return []

    proxies = [line.strip() for line in text.splitlines() if line.strip()]
    logger.info("%d proxies récupérés depuis l'API", len(proxies))
    return proxies


async def test_proxy(
    proxy_url: str,
    local_ip: str | None = None,
) -> tuple[str, bool, float, str | None]:
    """Teste un proxy en 2 étapes :

    1. Vérifie l'IP visible via ipify (rejette les proxies transparents)
    2. Vérifie que le proxy peut atteindre survivalworld.fr

    Retourne (proxy_url, success, latency_ms, proxy_ip).
    """
    start = time.monotonic()

    # Étape 1 : vérifier l'IP visible du proxy
    proxy_ip = None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                IP_CHECK_URL,
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=TEST_TIMEOUT),
                ssl=False,
            ) as resp:
                if resp.status == 200:
                    proxy_ip = (await resp.text()).strip()
                else:
                    return proxy_url, False, 0, None
    except Exception:
        return proxy_url, False, 0, None

    # Rejeter les proxies transparents (même IP que locale)
    if local_ip and proxy_ip == local_ip:
        logger.debug("Proxy %s transparent (même IP: %s), rejeté", proxy_url, proxy_ip)
        return proxy_url, False, 0, proxy_ip

    # Étape 2 : vérifier que le proxy peut atteindre le site cible
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                TARGET_TEST_URL,
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=TEST_TIMEOUT),
                ssl=False,
            ) as resp:
                # On accepte tout code HTTP (200, 301, 302, etc.)
                # L'important c'est que la connexion aboutisse
                latency = (time.monotonic() - start) * 1000
                return proxy_url, True, latency, proxy_ip
    except Exception:
        return proxy_url, False, 0, proxy_ip


async def find_working_proxies(
    count: int = 3,
    api_url: str = PROXYSCRAPE_API,
    local_ip: str | None = None,
    exclude_ips: list[str] | None = None,
) -> list[dict]:
    """Récupère et teste des proxies, retourne les `count` premiers fonctionnels.

    Teste par lots et s'arrête dès que `count` proxies fonctionnels sont trouvés.
    Rejette les proxies transparents (même IP que locale) et les IPs déjà utilisées.

    Retourne une liste de dicts :
    [{"url": "http://ip:port", "latency_ms": 123, "ip": "1.2.3.4"}, ...]
    """
    raw_proxies = await fetch_proxy_list(api_url)
    if not raw_proxies:
        return []

    # Détecter l'IP locale si pas fournie
    if local_ip is None:
        local_ip = await get_local_ip()

    # IPs à exclure (IP locale + proxies déjà assignés à d'autres comptes)
    excluded = set()
    if local_ip:
        excluded.add(local_ip)
    if exclude_ips:
        excluded.update(exclude_ips)

    # Lots plus gros pour compenser le double test (IP + site cible)
    batch_size = min(max(count * 5, 10), MAX_CONCURRENT_TESTS)
    logger.info(
        "Recherche de %d proxy(s) fonctionnel(s) parmi %d (lots de %d)...",
        count, len(raw_proxies), batch_size,
    )

    working: list[dict] = []
    tested = 0

    for i in range(0, len(raw_proxies), batch_size):
        if len(working) >= count:
            break

        batch = raw_proxies[i : i + batch_size]
        tested += len(batch)
        results = await asyncio.gather(
            *[test_proxy(p, local_ip) for p in batch]
        )

        for proxy_url, success, latency, proxy_ip in results:
            if success and proxy_ip and proxy_ip not in excluded:
                working.append({
                    "url": proxy_url,
                    "latency_ms": round(latency, 1),
                    "ip": proxy_ip,
                })
                # Empêcher d'assigner la même IP à plusieurs comptes
                excluded.add(proxy_ip)

        if len(working) >= count:
            break

    # Trier par latence (les plus rapides d'abord)
    working.sort(key=lambda p: p["latency_ms"])

    if working:
        logger.info(
            "%d proxy(s) fonctionnel(s) trouvé(s) après test de %d/%d (meilleur: %s [%s] en %.0fms)",
            len(working), tested, len(raw_proxies),
            working[0]["url"], working[0]["ip"], working[0]["latency_ms"],
        )
    else:
        logger.warning(
            "0/%d proxies fonctionnels après test de %d (tous échoués ou transparents)",
            len(raw_proxies), tested,
        )

    return working[:count]


async def assign_auto_proxies(accounts: list[dict]) -> list[dict]:
    """Assigne des proxies automatiques aux comptes configurés avec proxy: 'auto'.

    Modifie les comptes en place et retourne la liste mise à jour.
    """
    auto_accounts = [acc for acc in accounts if acc.get("proxy") == "auto"]
    if not auto_accounts:
        return accounts

    logger.info("%d compte(s) nécessitent un proxy automatique", len(auto_accounts))

    # Détecter l'IP locale d'abord
    local_ip = await get_local_ip()

    # Collecter les IPs des proxies manuels déjà configurés
    exclude_ips = []
    if local_ip:
        exclude_ips.append(local_ip)

    working = await find_working_proxies(
        count=len(auto_accounts),
        local_ip=local_ip,
        exclude_ips=exclude_ips,
    )

    if not working:
        logger.warning(
            "Aucun proxy fonctionnel trouvé ! Les comptes 'auto' voteront sans proxy."
        )
        for acc in auto_accounts:
            acc["proxy"] = None
            acc["_proxy_auto"] = True  # Marqueur pour retry plus tard
        return accounts

    # Assigner un proxy unique par compte
    for i, acc in enumerate(auto_accounts):
        if i < len(working):
            proxy_info = working[i]
            acc["proxy"] = proxy_info["url"]
            acc["_proxy_auto"] = True
            acc["_proxy_latency"] = proxy_info["latency_ms"]
            acc["_proxy_ip"] = proxy_info["ip"]
            logger.info(
                "[%s] Proxy auto assigné: %s (IP: %s, latence: %.0fms)",
                acc["pseudo"], proxy_info["url"], proxy_info["ip"],
                proxy_info["latency_ms"],
            )
        else:
            logger.warning(
                "[%s] Pas assez de proxies fonctionnels, vote sans proxy",
                acc["pseudo"],
            )
            acc["proxy"] = None
            acc["_proxy_auto"] = True

    return accounts
