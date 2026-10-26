# Monitor Clubika

Application privée de supervision, en français : le serveur, ses conteneurs, ses services et les applications hébergées, en temps réel. Elle regroupe ce que font Uptime Kuma (disponibilité, incidents, alertes) et Netdata (mesures du serveur).

**https://monitor.clubika.com** · Python (FastAPI + psutil), SQLite, interface web sans dépendance.

## Fonctionnalités

- **Vue d'ensemble** : état global, processeur, mémoire, disque, réseau, applications, conteneurs, incidents ouverts.
- **Serveur** (mesures toutes les 2 s, flux temps réel) : processeur et cœurs, charge moyenne, mémoire et swap, entrées/sorties disque, débit réseau, occupation des partitions, interfaces, processus les plus actifs, températures si disponibles ; historique de 1 h à 30 jours, export CSV.
- **Conteneurs Docker** : état, CPU, mémoire, réseau et disque de chacun.
- **Services système** : tous les services systemd.
- **Disponibilité** : contrôles HTTP(S), mot-clé, JSON, port TCP, DNS, ping, conteneur Docker, service systemd, certificat TLS et signal de vie (heartbeat) ; intervalle, délai, nombre d'échecs avant alerte ; pause, maintenance, vérification immédiate ; historique de latence ; expiration des certificats ; import/export.
- **Incidents et alertes** : ouverture et clôture automatiques (contrôles en échec, certificat proche de l'expiration, CPU/mémoire/disque au-delà d'un seuil pendant une durée donnée) ; notifications par **Telegram, webhook ou e-mail** ; journal des événements.
- **Page de statut publique** (`/status`, désactivée par défaut) : seuls les contrôles marqués « publics » y apparaissent.
- Thème clair/nuit, utilisable sur ordinateur et téléphone.

Au premier démarrage, les applications et conteneurs du serveur sont surveillés automatiquement (modifiable dans l'interface).

## Sécurité

Accès réservé à un administrateur : mot de passe haché (PBKDF2), session par cookie `HttpOnly`/`Secure`/`SameSite=Strict`, limitation des tentatives, protection CSRF (en-tête + origine), CSP stricte. Le mot de passe initial est généré au premier démarrage dans `admin-credentials.txt` (dossier de données) et supprimé à son premier changement. Les exports ne contiennent aucun secret.

## Hébergement

`deploy/install.sh` installe l'application comme **service systemd isolé** (utilisateur système `monitor` sans sudo ni accès à Docker, répertoire protégé en lecture seule), dans `/opt/monitor`, avec ses données dans `/var/lib/monitor`. Elle écoute sur un socket Unix ; deux conteneurs sans privilège l'entourent (`deploy/docker-compose.yml`) :

- `monitor-docker-proxy` : liste des conteneurs et statistiques **en lecture seule**, publiée uniquement sur `127.0.0.1` ;
- `monitor-bridge` : relie Caddy au socket Unix de l'application.

Le serveur web (Caddy) publie `https://monitor.clubika.com` vers `monitor-bridge:8000`. Mises à jour : `deploy/update.sh` (copie, dépendances, redémarrage, test de santé) ; le déploiement automatique du serveur l'exécute à chaque commit sur `main`.

## Développement

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
MONITOR_DATA=./data MONITOR_SECURE_COOKIE=0 MONITOR_ORIGIN=http://127.0.0.1:8000 .venv/bin/uvicorn app:app --port 8000
```

- `app.py` : API et authentification ; `engine.py` : mesures de l'hôte, contrôles, incidents, notifications ; `static/` : interface.
