# Monitor Clubika

Application privée de supervision, en français : le serveur, ses conteneurs, ses services et les applications hébergées, en temps réel. Elle regroupe ce que font Uptime Kuma (disponibilité, incidents, alertes) et Netdata (mesures du serveur).

**https://monitor.clubika.com** · Python (FastAPI + psutil), SQLite ou MariaDB, interface web sans dépendance.

## Fonctionnalités

- **Inventaire automatique** : douze groupes de collecte avec détection, état, fraîcheur, dernière réussite et explication des limites d'accès. Recherche par catégorie ou statut, détails progressifs, historique des changements, comparaison et export JSON/CSV.
- **Nouveaux indicateurs** : pression CPU/mémoire/E/S (PSI), limites cgroups du processus Monitor, timers, unités systemd installées et leurs dépendances/ressources/redémarrages, moteurs de bases détectés et états de sécurité accessibles. Aucun identifiant de base ni donnée métier n'est lu.
- **Cartographie Docker** : liens observés entre conteneurs, réseaux et volumes nommés, avec provenance. Un réseau partagé ne constitue pas une preuve de dépendance applicative à une base. Les montages privés et variables d'environnement ne sont pas inspectés.
- **Vue d'ensemble** : état global, processeur, mémoire, disque, réseau, applications, conteneurs, incidents ouverts.
- **Projets** : pour chaque application, un bouton pour **ouvrir l'application** et pour **ouvrir son dépôt GitHub**, son état et sa disponibilité, son certificat HTTPS, son conteneur (état, CPU, mémoire) et son **dernier déploiement**. Les conteneurs du serveur qui n'ont pas encore de projet sont **détectés automatiquement** et s'ajoutent en un clic. Les projets sont enregistrés dans la base de données.
- **Disponibilité** : contrôles HTTP(S), mot-clé, JSON, port TCP, DNS, ping, conteneur Docker, service systemd, certificat TLS et signal de vie (heartbeat) ; intervalle, délai, échecs avant alerte ; pause, maintenance, vérification immédiate ; historique de latence ; import/export.
- **Serveur** (mesures toutes les 2 s, flux temps réel) : processeur et cœurs, charge, mémoire et swap, entrées/sorties disque, débit réseau, occupation des partitions, interfaces, processus ; historique de 1 h à 30 jours, export CSV.
- **Système** : machine (système, noyau, architecture, virtualisation, fournisseur, fuseau, synchronisation NTP, version de Docker), processeur (modèle, fréquence, cache, changements de contexte), mémoire détaillée, processus et threads, descripteurs de fichiers, **stockage** (disques physiques, inodes, systèmes de fichiers montés, activité et E/S par disque), **réseau** (interfaces, adresses IP, MAC, vitesse, passerelle, DNS, connexions par état) et **ports ouverts** (TCP/UDP, service probable, exposition publique ou locale, conteneur qui les publie), **maintenance** (redémarrage requis, mises à jour disponibles dont sécurité, services systemd en échec, sessions ouvertes).
- **Logiciels** : tous les **paquets installés** (version, taille, installé à la main ou dépendance, mise à jour disponible, filtre), environnements d'exécution et outils détectés (Python, Node.js, Java, Go, PHP, Git, Docker, MariaDB…), images et volumes Docker.
- **Conteneurs Docker** : état, santé, durée de fonctionnement, redémarrages, ports publiés, CPU, mémoire, réseau, disque. **Services système** : tous les services systemd.
- **Incidents et alertes** : ouverture et clôture automatiques (contrôles en échec, certificat proche de l'expiration, CPU/mémoire/disque au-delà d'un seuil, services systemd en échec, inodes presque épuisés) ; notifications par **Telegram, webhook ou e-mail** ; journal des événements.
- **Page de statut publique** (`/status`, désactivée par défaut) ; thème clair/nuit ; utilisable sur ordinateur et téléphone.

## Fonctionne sur n'importe quel serveur

Rien n'est propre à un serveur dans le code : sans configuration, Monitor utilise **SQLite**, prend le nom d'hôte comme nom d'instance, accepte n'importe quel nom de domaine, et au premier démarrage il **détecte les conteneurs Docker** et le SSH pour les surveiller. Les réglages se font dans `/etc/monitor.env` (voir `deploy/monitor.env.example`) :

| Variable | Rôle |
|---|---|
| `MONITOR_NAME` | nom affiché (par défaut : nom d'hôte) |
| `MONITOR_ORIGIN` | adresse publique (alertes, contrôle d'origine) ; vide : l'hôte demandé |
| `MONITOR_DB` | `mysql://utilisateur:mot-de-passe@hôte/base` (ou `?socket=/run/mysqld/mysqld.sock`) pour **MariaDB / MySQL** ; vide : SQLite |
| `MONITOR_MIGRATE_FROM` | reprise unique d'une base SQLite lors du passage à MariaDB |
| `MONITOR_DEPLOY_STATE` | dossier des fichiers `{sha, at}` écrits par votre déploiement automatique (dernier déploiement des projets) |
| `MONITOR_COLLECTOR_INTERVALS` | objet JSON de fréquences en secondes, par identifiant de collecteur ; 10 à 86 400 s, configuration invalide remplacée par les valeurs par défaut |

Les informations sont lues dans le contexte et avec les permissions du service Monitor. Les paquets sont détectés via `dpkg-query` ou `rpm` : `apt-mark` reste facultatif. Sur RPM, l'origine manuel/dépendance et les mises à jour restent inconnues sans adaptateur de cache dédié ; Monitor ne lance ni installation ni rafraîchissement des dépôts. Les métriques internes des bases nécessitent une connexion dédiée autorisée et ne sont pas collectées dans cette version.

Par exemple, `MONITOR_COLLECTOR_INTERVALS={"pressure":60,"docker_topology":300}` ralentit ces deux collecteurs. Les collecteurs existants sont réutilisés, avec au plus deux collectes simultanées. L'historisation dispose d'un travailleur séparé et d'une file bornée : une écriture lente ne bloque pas les prélèvements hôte ou le planificateur. L'interface distingue une erreur de collecte d'un enregistrement en attente ou en échec.

L'historique d'inventaire utilise des tables additives `inventory_*`, séparées des mesures et incidents existants. Les limites applicatives sont 30 jours, 48 snapshots par groupe, 5 000 mesures et 1 000 changements par groupe ; les plafonds sont vérifiés au plus une fois par minute et peuvent limiter la période réellement visible. Un résultat persisté est borné à 256 Kio. Les compteurs volatils CPU/RAM/réseau ne produisent pas un événement de changement à chaque variation. Voir [les migrations et le retour arrière](migrations/README.md) et [la matrice de couverture / feuille de route](docs/server-inventory-roadmap.md).

Les API privées `/api/inventory`, `/history`, `/changes`, `/metrics`, `/compare`, `/relations` et `/export` (ces suffixes sous `/api/inventory`) lisent le cache ou la base. Elles ne déclenchent pas de commandes système. Le schéma s'applique au démarrage ; une migration SQLite vers MariaDB est terminée **avant** le démarrage des collecteurs afin de conserver les identifiants des historiques.

## Sécurité

Accès réservé à un administrateur : mot de passe haché (PBKDF2), session par cookie `HttpOnly`/`Secure`/`SameSite=Strict`, limitation des tentatives, protection CSRF (en-tête + origine), CSP stricte. Le mot de passe initial est généré au premier démarrage dans `admin-credentials.txt` (dossier de données) et supprimé à son premier changement. Les exports ne contiennent aucun secret, et les variables d'environnement des conteneurs ne sont jamais lues.

## Hébergement

`deploy/install.sh` installe l'application comme **service systemd isolé** (utilisateur système `monitor` sans sudo ni accès à Docker, répertoire protégé en lecture seule), dans `/opt/monitor`, avec ses données dans `/var/lib/monitor`. Elle écoute sur un socket Unix ; deux conteneurs sans privilège l'entourent (`deploy/docker-compose.yml`) :

- `monitor-docker-proxy` : liste des conteneurs, images, volumes et statistiques **en lecture seule**, publiée uniquement sur `127.0.0.1` ;
- `monitor-bridge` : relie Caddy au socket Unix de l'application.

Le serveur web (Caddy) publie `https://monitor.clubika.com` vers `monitor-bridge:8000`. Mises à jour : `deploy/update.sh` (copie, dépendances, redémarrage, test de santé) ; le déploiement automatique du serveur l'exécute à chaque commit sur `main`.

## Développement

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
node --test tests/test_inventory_ui.js
MONITOR_DATA=./data MONITOR_SECURE_COOKIE=0 MONITOR_ORIGIN=http://127.0.0.1:8000 .venv/bin/uvicorn app:app --port 8000
```

- `app.py` : API et authentification ; `engine.py` : mesures de l'hôte, contrôles, incidents, notifications ; `static/` : interface.
- `collector_runtime.py` et `host_collectors.py` : ordonnancement borné et collecteurs optionnels ; `inventory_store.py` : historique et migration ; `software_inventory.py` et `docker_topology.py` : adaptateurs spécialisés.
- Les tests MariaDB réels nécessitent `MONITOR_MARIADB_TEST_SOCKET` pointant explicitement vers une instance **isolée** : ils créent et retirent une base de test aléatoire. Ils n'utilisent pas `MONITOR_DB` et refusent les chemins de sockets de production habituels.
- Le test navigateur facultatif `tests/e2e_inventory.py` utilise un Chromium et Playwright déjà installés, avec `MONITOR_BROWSER_URL`, `MONITOR_BROWSER_DATA` et `MONITOR_BROWSER_ARTIFACTS` dédiés à une instance locale de test. Aucun téléchargement de navigateur automatique.
