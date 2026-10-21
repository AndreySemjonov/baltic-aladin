"""One job run inside its container, measured (the owner, 21.10.2026: before choosing hardware, a
48-72-hour report of each job's real cost): its command run to the end, then one line of JSON appended
to /stats/<job>.jsonl:

    {"job", "start", "end", "wall_s", "cpu_user_s", "cpu_sys_s", "peak_rss_mb", "net_in_mb", "net_out_mb",
     "out_mb", "disk_peak_mb", "exit", "worked", "tail"}

CPU and the peak memory are the job's processes' own (getrusage of the children); the network the
container's own interface (each run in its own container: other jobs' traffic isn't counted); out_mb what
it wrote to --local (what it would have uploaded); disk_peak_mb the most /work held (sampled every 10 s).
"worked": False when its output says there was nothing new ("already published", "Nothing new").

    python /measure.py <job> -- <command ...>
"""
import json
import os
import resource
import subprocess
import sys
import threading
import time

QUIET = ("already published", "Nothing new", "nothing new", "is published already")


def net_bytes():
    """Bytes in and out over the container's interfaces (lo left out)."""
    rx = tx = 0
    with open("/proc/net/dev") as f:
        for line in f.readlines()[2:]:
            name, data = line.split(":", 1)
            if name.strip() == "lo":
                continue
            fields = data.split()
            rx += int(fields[0])
            tx += int(fields[8])
    return rx, tx


def folder_bytes(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def main():
    job = sys.argv[1]
    command = sys.argv[sys.argv.index("--") + 1:]
    os.makedirs("/work/tmp", exist_ok=True)
    os.makedirs("/stats", exist_ok=True)
    peak = [0]
    done = threading.Event()

    def watch_disk():
        while not done.wait(10):
            peak[0] = max(peak[0], folder_bytes("/work"))
    threading.Thread(target=watch_disk, daemon=True).start()

    rx0, tx0 = net_bytes()
    start = time.time()
    tail = []
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                               env=dict(os.environ, PYTHONUNBUFFERED="1", TMPDIR="/work/tmp"))
    for line in process.stdout:
        sys.stdout.write(line)
        tail = (tail + [line.rstrip()])[-12:]
    code = process.wait()
    end = time.time()
    done.set()
    rx1, tx1 = net_bytes()
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    out = folder_bytes("/work/out")
    record = {
        "job": job, "start": round(start), "end": round(end), "wall_s": round(end - start, 1),
        "cpu_user_s": round(usage.ru_utime, 1), "cpu_sys_s": round(usage.ru_stime, 1),
        "peak_rss_mb": round(usage.ru_maxrss / 1024),
        "net_in_mb": round((rx1 - rx0) / 1e6, 1), "net_out_mb": round((tx1 - tx0) / 1e6, 1),
        "out_mb": round(out / 1e6, 1), "disk_peak_mb": round(max(peak[0], folder_bytes("/work")) / 1e6),
        "exit": code, "worked": not any(q in line for line in tail for q in QUIET), "tail": tail[-3:],
    }
    with open(f"/stats/{job}.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")
    sys.exit(code)


if __name__ == "__main__":
    main()
