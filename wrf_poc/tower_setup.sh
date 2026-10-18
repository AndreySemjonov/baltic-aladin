#!/usr/bin/env bash
# The home PC "Tower" (i7-12700K, 32 GB; the owner, 18.10.2026: two WRF runs a day for a week's test
# before the Mac mini): everything wrf_poc/tower.sh needs, inside WSL's Ubuntu, as root. Run once:
#
#   wsl -d Ubuntu-24.04 -u root
#   curl -fsSL https://raw.githubusercontent.com/AndreySemjonov/baltic-aladin/main/wrf_poc/tower_setup.sh | bash
#   gh auth login            (GitHub.com, HTTPS, log in with a web browser: the uploads and the publish)
#
# Then a test run: bash /root/baltic-aladin/wrf_poc/tower.sh tarifa 6
set -euo pipefail
log() { echo "$(date -u +%H:%M:%S) $*"; }

log "packages (Docker, Python, git, the GitHub command line)"
apt-get update -qq
apt-get install -y -qq docker.io python3-venv python3-pip git curl ca-certificates > /dev/null
if ! command -v gh > /dev/null; then
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update -qq
  apt-get install -y -qq gh > /dev/null
fi
service docker start > /dev/null 2>&1 || true

log "the helper's code"
if [ -d /root/baltic-aladin/.git ]; then
  git -C /root/baltic-aladin pull -q --rebase --autostash
else
  git clone -q https://github.com/AndreySemjonov/baltic-aladin.git /root/baltic-aladin
fi

log "the WRF container"
docker pull -q ncar/iwrf:latest > /dev/null
log "the terrain data (about 30 GB, once)"
if [ ! -d /root/geog/WPS_GEOG ]; then
  mkdir -p /root/geog
  curl -sSL https://www2.mmm.ucar.edu/wrf/src/wps_files/geog_high_res_mandatory.tar.gz | tar -xz -C /root/geog
fi
mkdir -p /root/wrf-logs
log "ready: run 'gh auth login' once, then a test: bash /root/baltic-aladin/wrf_poc/tower.sh tarifa 6"
