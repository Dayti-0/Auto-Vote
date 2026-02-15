# Minecraft Auto-Voter pour SurvivalWorld

## Objectif

Programme Python qui vote automatiquement pour le serveur Minecraft SurvivalWorld sur 3 sites de vote, en boucle, avec des timers indépendants par site. Doit être léger en ressources (remplace une solution Actiona trop gourmande).

## Stack technique

- **Python 3.11+**
- **Playwright** (pas Selenium — plus léger, meilleur gestion des onglets)
- **PyYAML** pour la config
- **APScheduler** pour les timers indépendants
- **Rich** pour les logs console (optionnel mais joli)

## Contexte du site survivalworld.fr/vote

Le site `https://survivalworld.fr/vote` contient :
1. Un champ texte pour saisir le pseudo Minecraft + bouton "Continuer"
2. Après validation du pseudo, 5 boutons de vote apparaissent (liens vers des sites externes)
3. Chaque bouton ouvre un site de vote dans un nouvel onglet

**IMPORTANT** : Les URLs des boutons contiennent `{player}` comme placeholder pour le pseudo. Exemple :
- `https://serveur-minecraft-vote.fr/serveurs/survivalworld.229/vote?pseudo={player}`
- `https://serveur-prive.net/minecraft/survivalworld/vote`
- `https://serveur-minecraft.com/4224`

## Sites de vote à implémenter (3 sur 5)

### Site 1 : serveur-minecraft-vote.fr (toutes les 1h30)
- **URL** : `https://serveur-minecraft-vote.fr/serveurs/survivalworld.229/vote?pseudo={pseudo}`
- **Procédure** :
  1. Naviguer vers l'URL (le pseudo est dans l'URL)
  2. Attendre le chargement de la page
  3. Trouver et cliquer sur le bouton "Voter" sur la page
  4. Attendre confirmation du vote
  5. Fermer la page
- **Intervalle** : 90 minutes + marge aléatoire de 2-5 min

### Site 2 : serveur-prive.net (toutes les 1h30)
- **URL** : `https://serveur-prive.net/minecraft/survivalworld/vote`
- **Procédure** :
  1. Naviguer vers l'URL
  2. Attendre le chargement complet de la page (le vote est comptabilisé au chargement)
  3. Fermer la page
- **Intervalle** : 90 minutes + marge aléatoire de 2-5 min

### Site 3 : serveur-minecraft.com (toutes les 3h)
- **URL** : `https://serveur-minecraft.com/4224`
- **Procédure** :
  1. Naviguer vers l'URL
  2. Attendre le chargement complet de la page (le vote est comptabilisé au chargement)
  3. Fermer la page
- **Intervalle** : 180 minutes + marge aléatoire de 2-5 min

### Sites EXCLUS (protections anti-bot)
- ❌ top-serveurs.net → Protection Cloudflare
- ❌ serveursminecraft.org → Recaptcha

## Architecture du projet

```
minecraft-voter/
├── CLAUDE.md
├── config.yaml           # Configuration utilisateur
├── requirements.txt
├── main.py               # Point d'entrée, boucle principale
├── scheduler.py          # Gestion des timers indépendants par site
├── browser.py            # Gestion du navigateur Playwright (init, cleanup)
├── logger_setup.py       # Configuration du logging (fichier + console)
├── voters/
│   ├── __init__.py
│   ├── base.py           # Classe abstraite BaseVoter
│   ├── serveur_minecraft_vote.py   # 1h30 - clic sur "Voter"
│   ├── serveur_prive.py            # 1h30 - juste charger la page
│   └── serveur_minecraft.py        # 3h - juste charger la page
└── logs/
    └── votes.log         # Fichier de log rotatif
```

## Fichier config.yaml

```yaml
# Pseudo Minecraft (obligatoire)
pseudo: "CHANGE_ME"

# Mode navigateur
headless: true          # true = invisible, false = voir le navigateur (debug)
slow_mo: 0              # Millisecondes de délai entre chaque action (0 en prod, 500+ pour debug)

# Configuration par site
sites:
  serveur_minecraft_vote:
    enabled: true
    interval_minutes: 90
    random_delay_max: 5   # Marge aléatoire en minutes ajoutée à l'intervalle
  serveur_prive:
    enabled: true
    interval_minutes: 90
    random_delay_max: 5
  serveur_minecraft:
    enabled: true
    interval_minutes: 180
    random_delay_max: 5

# Logging
log_level: "INFO"         # DEBUG pour plus de détails
log_file: "logs/votes.log"
```

## Spécifications techniques détaillées

### Classe BaseVoter (voters/base.py)
```python
from abc import ABC, abstractmethod

class BaseVoter(ABC):
    def __init__(self, name: str, url: str, interval_minutes: int, random_delay_max: int):
        self.name = name
        self.url = url
        self.interval_minutes = interval_minutes
        self.random_delay_max = random_delay_max
        self.last_vote_time = None
        self.vote_count = 0
        self.consecutive_failures = 0

    @abstractmethod
    async def vote(self, page) -> bool:
        """Effectue le vote. Retourne True si succès, False sinon."""
        pass

    def can_vote(self) -> bool:
        """Vérifie si assez de temps s'est écoulé depuis le dernier vote."""
        pass
```

### Gestion du navigateur (browser.py)
- Utiliser Playwright en mode **async**
- Lancer UN SEUL navigateur Chromium persistant
- Pour chaque vote : ouvrir un nouvel onglet → voter → fermer l'onglet
- Configurer un **user-agent réaliste** pour éviter la détection
- Ajouter des **délais aléatoires** entre les actions (humanisation)
- Gérer les timeouts (30 secondes max par page)
- En cas d'échec : log l'erreur, ne pas crash, réessayer au prochain cycle

### Scheduler (scheduler.py)
- Chaque site a son propre timer indépendant
- Au démarrage : voter immédiatement sur tous les sites activés
- Ensuite : chaque site revote après son intervalle + délai aléatoire
- Afficher dans la console le prochain vote prévu pour chaque site
- Utiliser asyncio pour la boucle principale (pas besoin d'APScheduler finalement, asyncio suffit)

### Boucle principale (main.py)
- Charger la config YAML
- Vérifier que le pseudo n'est pas "CHANGE_ME"
- Installer Playwright si nécessaire (`playwright install chromium`)
- Initialiser le navigateur
- Lancer les voters en parallèle avec asyncio
- Afficher un récap console : votes réussis, prochains votes, erreurs
- Gérer CTRL+C proprement (fermer le navigateur)

### Logging (logger_setup.py)
- Double sortie : console (coloré) + fichier rotatif
- Format : `[2025-02-15 14:30:00] [INFO] [serveur_prive] Vote réussi (#42) - Prochain vote à 16:05`
- Rotation : max 5 fichiers de 1MB

## Comportement anti-détection

- **User-agent** : utiliser un user-agent Chrome récent et réaliste
- **Délais aléatoires** : entre 1-3 secondes avant chaque clic
- **Viewport** : taille réaliste (1920x1080 ou 1366x768)
- **Ne PAS** utiliser les flags `--disable-blink-features=AutomationControlled` car Playwright les gère déjà
- **Intervalles non-fixes** : ajouter une marge aléatoire de 2-5 min à chaque cycle

## Gestion des erreurs

- Si un vote échoue : log l'erreur, continuer avec les autres sites
- Si 3 échecs consécutifs sur un site : log un WARNING, continuer quand même
- Si le navigateur crash : le relancer automatiquement
- Timeout de 30 secondes par page, 10 secondes par action
- Jamais de crash total du programme

## Affichage console au démarrage

```
╔══════════════════════════════════════════╗
║     🗳️  Minecraft Auto-Voter v1.0       ║
║     Serveur: SurvivalWorld               ║
║     Pseudo: MonPseudo                    ║
╠══════════════════════════════════════════╣
║  Sites actifs:                           ║
║  ✅ serveur-minecraft-vote.fr (1h30)     ║
║  ✅ serveur-prive.net (1h30)             ║
║  ✅ serveur-minecraft.com (3h)           ║
║  ❌ top-serveurs.net (Cloudflare)        ║
║  ❌ serveursminecraft.org (Recaptcha)    ║
╠══════════════════════════════════════════╣
║  Mode: headless                          ║
║  Votes lancés...                         ║
╚══════════════════════════════════════════╝
```

## Affichage en cours d'exécution

Afficher périodiquement (toutes les 5 minutes ou après chaque vote) un statut :
```
[14:30:00] ✅ serveur-prive.net     - Vote #12 réussi - Prochain: 16:05
[14:30:05] ✅ serveur-minecraft.com - Vote #8 réussi  - Prochain: 17:35
[14:30:10] ❌ serveur-minecraft-vote.fr - Échec (timeout) - Retry: 16:07
```

## Instructions d'installation

Le README.md doit contenir :
```bash
git clone <repo>
cd minecraft-voter
pip install -r requirements.txt
playwright install chromium
# Éditer config.yaml avec son pseudo
python main.py
```

## Requirements.txt

```
playwright>=1.40.0
pyyaml>=6.0
```

## Notes importantes

1. Le programme doit être **très léger** en mémoire/CPU (c'est tout le point vs Actiona)
2. Playwright headless est beaucoup plus léger qu'un navigateur visible
3. Ne garder le navigateur ouvert que pendant les votes, le fermer entre les cycles si possible pour économiser les ressources — OU garder une seule instance et juste ouvrir/fermer des onglets
4. Le programme est destiné à tourner 24/7 sur un PC
5. Tester en mode `headless: false` d'abord pour vérifier que les clics fonctionnent
