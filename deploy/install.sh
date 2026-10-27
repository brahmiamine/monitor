#!/usr/bin/env bash
# Installe (ou met à jour) Monitor sur le serveur. À lancer depuis le dépôt : bash deploy/install.sh
# Crée l'utilisateur système « monitor », copie l'application dans /opt/monitor, installe le service systemd
# et démarre les deux conteneurs d'appui.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

id monitor >/dev/null 2>&1 || sudo useradd --system --no-create-home --home-dir /var/lib/monitor --shell /usr/sbin/nologin monitor
sudo install -d -o monitor -g monitor -m 0750 /var/lib/monitor
sudo install -d -o monitor -g monitor -m 0755 /var/lib/monitor/run
sudo install -d -o root -g root -m 0755 /opt/monitor /opt/monitor/app
sudo install -d -o "$(id -un)" -m 0755 /opt/monitor/deploy-state   # lisible par Monitor, écrit par le déploiement automatique

"$REPO/deploy/update.sh" --no-restart
[ -f /etc/monitor.env ] || sudo install -m 0640 -o root -g monitor "$REPO/deploy/monitor.env.example" /etc/monitor.env
sudo cp "$REPO/deploy/monitor.service" /etc/systemd/system/monitor.service
sudo cp "$REPO/deploy/monitor-backup.service" /etc/systemd/system/monitor-backup.service
sudo cp "$REPO/deploy/monitor-backup.timer" /etc/systemd/system/monitor-backup.timer
sudo systemctl daemon-reload
# Preserve an existing monitor-bridge network during upgrades to avoid disconnecting Caddy.
# Fresh servers receive a dedicated network; the reverse proxy can join it explicitly.
if [ -z "${MONITOR_PROXY_NETWORK:-}" ]; then
  EXISTING_NETWORK="$(docker inspect monitor-bridge --format '{{range $name, $network := .NetworkSettings.Networks}}{{println $name}}{{end}}' 2>/dev/null | head -n 1 || true)"
  export MONITOR_PROXY_NETWORK="${EXISTING_NETWORK:-monitor_proxy}"
fi
docker network inspect "$MONITOR_PROXY_NETWORK" >/dev/null 2>&1 || docker network create "$MONITOR_PROXY_NETWORK" >/dev/null
(cd "$REPO/deploy" && docker compose up -d)
sudo systemctl enable --now monitor.service
sudo systemctl enable --now monitor-backup.timer
sudo systemctl restart monitor.service
echo "Monitor installé. Identifiants initiaux : sudo cat /var/lib/monitor/admin-credentials.txt"
