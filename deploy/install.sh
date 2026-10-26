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

"$REPO/deploy/update.sh" --no-restart
sudo cp "$REPO/deploy/monitor.service" /etc/systemd/system/monitor.service
sudo systemctl daemon-reload
(cd "$REPO/deploy" && docker compose up -d)
sudo systemctl enable --now monitor.service
sudo systemctl restart monitor.service
echo "Monitor installé. Identifiants initiaux : sudo cat /var/lib/monitor/admin-credentials.txt"
