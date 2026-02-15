import asyncio
import logging
import time

import aiohttp

logger = logging.getLogger("auto-voter")

# API ProxyScrape — proxies gratuits français (HTTP/SOCKS4/SOCKS5)
PROXYSCRAPE_API = (
    "https://api.proxyscrape.com/v4/free-proxy-list/get"
    "?request=display_proxies"
    "&country=fr"
    "&proxy_format=protocolipport"
    "&format=text"
    "&timeout=20000"
)

# URL légère pour tester la connectivité d'un proxy
TEST_URL = "https://httpbin.org/ip"
# Timeout pour le test de chaque proxy (secondes)
TEST_TIMEOUT = 8
# Nombre max de proxies à tester en parallèle
MAX_CONCURRENT_TESTS = 20


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


async def test_proxy(proxy_url: str, test_url: str = TEST_URL) -> tuple[str, bool, float]:
    """Teste un proxy en faisant une requête HTTP.

    Retourne (proxy_url, success, latency_ms).
    """
    start = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                test_url,
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=TEST_TIMEOUT),
                ssl=False,
            ) as resp:
                if resp.status == 200:
                    latency = (time.monotonic() - start) * 1000
                    return proxy_url, True, latency
                return proxy_url, False, 0
    except Exception:
        return proxy_url, False, 0


async def find_working_proxies(
    count: int = 3,
    api_url: str = PROXYSCRAPE_API,
) -> list[dict]:
    """Récupère et teste des proxies, retourne les `count` premiers fonctionnels.

    Teste par lots et s'arrête dès que `count` proxies fonctionnels sont trouvés,
    au lieu de tester systématiquement tous les proxies.

    Retourne une liste de dicts : [{"url": "http://ip:port", "latency_ms": 123}, ...]
    """
    raw_proxies = await fetch_proxy_list(api_url)
    if not raw_proxies:
        return []

    # Taille de lot : au moins count * 3 pour avoir de la marge, plafonné au max concurrent
    batch_size = min(max(count * 3, 5), MAX_CONCURRENT_TESTS)
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
        results = await asyncio.gather(*[test_proxy(p) for p in batch])

        for proxy_url, success, latency in results:
            if success:
                working.append({"url": proxy_url, "latency_ms": round(latency, 1)})

        if len(working) >= count:
            break

    # Trier par latence (les plus rapides d'abord)
    working.sort(key=lambda p: p["latency_ms"])

    if working:
        logger.info(
            "%d proxy(s) fonctionnel(s) trouvé(s) après test de %d/%d (meilleur: %s en %.0fms)",
            len(working), tested, len(raw_proxies),
            working[0]["url"], working[0]["latency_ms"],
        )
    else:
        logger.info(
            "0/%d proxies fonctionnels après test de %d",
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

    working = await find_working_proxies(count=len(auto_accounts))

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
            logger.info(
                "[%s] Proxy auto assigné: %s (latence: %.0fms)",
                acc["pseudo"], proxy_info["url"], proxy_info["latency_ms"],
            )
        else:
            logger.warning(
                "[%s] Pas assez de proxies fonctionnels, vote sans proxy",
                acc["pseudo"],
            )
            acc["proxy"] = None
            acc["_proxy_auto"] = True

    return accounts
