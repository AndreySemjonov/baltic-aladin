#!/usr/bin/env bash
# The helper's runner on this machine (the owner, 21.10.2026: the Tower, then the Mac mini): the newest
# code, the image (rebuilt when runner/Dockerfile or measure.py changed), then the scheduler in the
# foreground. Started at logon by Windows' Task Scheduler ("MeteoGust runner": wsl.exe -d Ubuntu -u root
# -- bash /root/baltic-aladin/runner/run.sh), which keeps Ubuntu up while it runs.
set -uo pipefail
REPO=/root/baltic-aladin
mkdir -p /var/lib/meteogust/logs
exec > >(tee -a /var/lib/meteogust/logs/runner.log) 2>&1
service docker start > /dev/null 2>&1 || true
git -C "$REPO" pull -q --rebase --autostash || echo "git pull failed: the code as it is"
stamp=$(cat "$REPO/runner/Dockerfile" "$REPO/runner/measure.py" | sha1sum | cut -c1-12)
if ! docker image inspect "meteogust-helper:$stamp" > /dev/null 2>&1; then
  echo "building the image ($stamp)"
  docker build -q -t "meteogust-helper:$stamp" -t meteogust-helper "$REPO/runner" || exit 1
fi
exec python3 "$REPO/runner/scheduler.py"
