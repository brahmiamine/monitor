#!/usr/bin/env bash
# Copie l'application dans /opt/monitor/app, met à jour ses dépendances et redémarre le service.
# Utilisé par install.sh et par le déploiement automatique. Option --no-restart : ne pas redémarrer.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
sudo rsync -a --delete --chown=root:root --chmod=D755,F644 app.py engine.py backup_util.py collector_runtime.py host_collectors.py inventory_store.py docker_topology.py software_inventory.py static requirements.txt /opt/monitor/app/
[ -x /opt/monitor/venv/bin/pip ] || sudo python3 -m venv /opt/monitor/venv
sudo /opt/monitor/venv/bin/pip install --quiet --disable-pip-version-check -r /opt/monitor/app/requirements.txt
[ "${1:-}" = "--no-restart" ] && exit 0
sudo systemctl restart monitor.service
# Test de santé : l'application doit répondre sur son socket Unix.
for _ in $(seq 1 30); do
  sudo curl -sf --unix-socket /var/lib/monitor/run/monitor.sock http://localhost/healthz >/dev/null 2>&1 && exit 0
  sleep 2
done
echo "Monitor ne répond pas après la mise à jour" >&2
exit 1
