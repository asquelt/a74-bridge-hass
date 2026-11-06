#!/bin/sh
# Installs/updates the a74_bridge custom integration on Kiiro over SSH (no HACS: the repo is on a private Gitea).
# Usage: sh install.sh [restart]   (fallback when HACS is not used)
set -eu
KEY="${KIIRO_KEY:-$HOME/hass/kiiro/hass-key}"
HOST="${KIIRO_HOST:-hassio@10.242.0.6}"
SSH="ssh -i $KEY -p 2222 -o ConnectTimeout=15 $HOST"
cd "$(dirname "$0")"
COPYFILE_DISABLE=1 tar --no-xattrs --exclude='._*' --exclude='.DS_Store' --exclude='__pycache__' -czf /tmp/a74_bridge_ha.tgz custom_components/a74_bridge
$SSH 'cat > /tmp/a74_bridge_ha.tgz' < /tmp/a74_bridge_ha.tgz
$SSH 'cd /config && sudo -n rm -rf custom_components/a74_bridge && sudo -n tar xzf /tmp/a74_bridge_ha.tgz && sudo -n mkdir -p /share/camera_presets && find custom_components/a74_bridge -type f | wc -l'
if [ "${1:-}" = "restart" ]; then $SSH 'bash -lc "ha core restart"'; echo "HA Core restart requested"; fi
