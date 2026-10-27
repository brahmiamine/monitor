# Monitor Clubika

Application privée de supervision, en français : le serveur, ses conteneurs, ses services et les applications hébergées, en temps réel. Elle regroupe ce que font Uptime Kuma (disponibilité, incidents, alertes) et Netdata (mesures du serveur).

**https://monitor.clubika.com** · Python (FastAPI + psutil), SQLite ou MariaDB, interface web sans dépendance.

## Fonctionnalités

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

Les informations système, les paquets et les conteneurs sont lus directement sur la machine (Debian/Ubuntu pour la liste des paquets ; le reste fonctionne sur tout Linux).

## Sécurité

Accès réservé à un administrateur : mot de passe haché (PBKDF2), session par cookie `HttpOnly`/`Secure`/`SameSite=Strict`, limitation des tentatives, protection CSRF (en-tête + origine), CSP stricte. Le mot de passe initial est généré au premier démarrage dans `admin-credentials.txt` (dossier de données) et supprimé à son premier changement. Les exports ne contiennent aucun secret, et les variables d'environnement des conteneurs ne sont jamais lues.

## Hébergement

`deploy/install.sh` installe l'application comme **service systemd isolé** (utilisateur système `monitor` sans sudo ni accès à Docker, répertoire protégé en lecture seule), dans `/opt/monitor`, avec ses données dans `/var/lib/monitor`. Elle écoute sur un socket Unix ; deux conteneurs sans privilège l'entourent (`deploy/docker-compose.yml`) :

- `monitor-docker-proxy` : liste des conteneurs, images, volumes et statistiques **en lecture seule**, publiée uniquement sur `127.0.0.1` ;
- `monitor-bridge` : relie Caddy au socket Unix de l'application.

Le reverse proxy (Caddy, Traefik, Nginx…) se connecte au réseau Docker `monitor_proxy` par défaut (créé automatiquement par `deploy/install.sh`) et transmet vers `monitor-bridge:8000`. Exemple pour Caddy dans un autre conteneur : `docker network connect monitor_proxy NOM_DU_CONTENEUR_CADDY`, puis `reverse_proxy monitor-bridge:8000`. **L'installation ne suppose plus de réseau `clubika_internal`**, de domaine ou de conteneur de proxy prédéfini. Ce raccordement au proxy reste une étape à configurer selon le serveur. Mises à jour : `deploy/update.sh` (copie, dépendances, redémarrage, test de santé) ; le déploiement automatique du serveur l'exécute à chaque commit sur `main`.

## Développement

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
MONITOR_DATA=./data MONITOR_SECURE_COOKIE=0 MONITOR_ORIGIN=http://127.0.0.1:8000 .venv/bin/uvicorn app:app --port 8000
```

- `app.py` : API et authentification ; `engine.py` : mesures de l'hôte, contrôles, incidents, notifications ; `static/` : interface.

## Fiabilité des alertes

Les envois Telegram, webhook et SMTP (STARTTLS ou TLS direct/465) sont enregistrés dans `notification_jobs` avant transmission. En cas d'échec, chaque canal est retenté avec un délai croissant (8 essais maximum), même après redémarrage. Les livraisons sont consultables via `/api/notifications/deliveries` (authentifié). Le contrôle `/healthz` renvoie HTTP 503 si les métriques sont anciennes ou si un collecteur Docker/systemd échoue. Un contrôle **hors du VPS** est indispensable pour détecter l'arrêt complet de la machine.

Les conteneurs arrêtés, en redémarrage, marqués unhealthy ou OOM déclenchent des incidents dédiés. Évitez de maintenir des conteneurs volontairement arrêtés sans maintenance lorsque les alertes Docker sont actives.


## Supervision applicative et diagnostics

Les projets peuvent être reliés explicitement à plusieurs contrôles. Les associations implicites anciennes continuent de fonctionner lorsqu'aucune association explicite n'a été définie. Les pages **Rapports de disponibilité** (1, 7, 30, 90 jours) et **Base de données** exposent la couverture temporelle et les principales statistiques sans identifiants. Les logs Docker sont accessibles aux administrateurs dans la page Conteneurs (maximum 500 lignes, résultat borné). La PWA conserve uniquement les ressources statiques, jamais les API ou les sessions.

## Sauvegardes programmées et restauration

`deploy/install.sh` installe aussi `monitor-backup.timer` (sauvegarde quotidienne vers `/var/lib/monitor/backups`, permissions 0600, conservation 14 jours par défaut via `MONITOR_BACKUP_RETENTION_DAYS`). SQLite utilise `sqlite3.Connection.backup()`. MariaDB requiert **`mariadb-dump` ou `mysqldump` présent sur le serveur** ; sinon le job échoue (visible dans systemd). Les données sauvegardées contiennent des secrets : conserver ces archives privées, tester la restauration et répliquer hors VPS pour protéger contre une panne du disque. Pour restaurer, arrêter Monitor, prendre une copie de la base actuelle, puis exécuter `python backup_util.py restore CHEMIN --confirm`. En SQLite, déplacer la base existante hors du dossier avant la restauration.

## Sonde extérieure indépendante

Pour détecter un VPS complètement arrêté, faire exécuter `tools/external_watch.py` **sur une autre machine**. Une GitHub Actions facultative tourne toutes les 5 min : créer la variable de dépôt `MONITOR_EXTERNAL_ENABLED=true`, ainsi que les secrets `MONITOR_HEALTH_URL=https://votre-domaine/healthz`, `MONITOR_TELEGRAM_TOKEN` et `MONITOR_TELEGRAM_CHAT_ID`. Par défaut elle est désactivée et n'impose ni domaine ni fournisseur. Les alertes peuvent se répéter toutes les cinq minutes pendant une panne.

## Portabilité

Nécessite Linux, systemd, Docker et Python. L'application ne dépend pas d'OVH ni d'un domaine particulier. Le réseau Docker de la passerelle est créé automatiquement par `deploy/install.sh` (`monitor_proxy` ou `MONITOR_PROXY_NETWORK`). Sur une installation existante, le script conserve le réseau actuel de `monitor-bridge` pour éviter de déconnecter Caddy. Lancer Compose manuellement nécessite de créer le réseau externe au préalable. Brancher explicitement le conteneur du reverse proxy sur ce réseau et router vers `monitor-bridge:8000`. L'hébergement HTTPS et son domaine restent à configurer selon le serveur.
