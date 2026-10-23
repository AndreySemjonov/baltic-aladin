"""Whether the home runner makes these jobs live now (the owner, 23.10.2026: the jobs move to the Tower in groups,
GitHub kept as the backup): prints home=true when its heartbeat (files.meteogust.com/runner/heartbeat.json, under 20
minutes old, as helper-trigger reads it) lists every one of them live, else home=false; a workflow's step then leaves
its run to home. A job that fails twice in a row at home leaves the heartbeat's list, and GitHub makes it again.

    python3 home_live.py JOB [JOB...] >> "$GITHUB_OUTPUT"      (no packages needed: the first step after the checkout)
"""
import json
import sys
import time
import urllib.request

HEARTBEAT = "https://files.meteogust.com/runner/heartbeat.json"


def main():
    jobs = sys.argv[1:]
    try:
        request = urllib.request.Request(f"{HEARTBEAT}?t={int(time.time())}", headers={"User-Agent": "baltic-aladin home_live.py"})
        with urllib.request.urlopen(request, timeout=20) as reply:
            beat = json.load(reply)
        live = set(beat.get("live") or []) if time.time() - beat.get("t", 0) < 1200 else set()
    except Exception as error:
        print(f"heartbeat unread ({error}): made here", file=sys.stderr)
        live = set()
    home = bool(jobs) and all(job in live for job in jobs)
    print(f"{', '.join(jobs)}: {'made live at home now, left out here' if home else 'made here'}", file=sys.stderr)
    print("home=" + ("true" if home else "false"))


if __name__ == "__main__":
    main()
