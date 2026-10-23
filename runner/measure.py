"""One job run inside its container, measured (the owner, 21.10.2026: before choosing hardware, a
48-72-hour report of each job's real cost): its command run to the end, then one line of JSON appended
to /stats/<job>.jsonl:

    {"job", "start", "end", "wall_s", "cpu_user_s", "cpu_sys_s", "peak_rss_mb", "net_in_mb", "net_out_mb",
     "out_mb", "disk_peak_mb", "exit", "worked", "tail"}

CPU is the job's processes' own (getrusage of the children), the peak memory the biggest process's or the
container's sampled total, whichever is higher; the network the
container's own interface (each run in its own container: other jobs' traffic isn't counted); out_mb what
it wrote to --local (what it would have uploaded); disk_peak_mb the most /work held (sampled every 10 s).
"worked": False when its output says there was nothing new ("already published", "Nothing new").

Its use over the run (the owner, 23.10.2026: "on card also graph for usage ... CPU, RAM ... during its run"): every
USAGE_STEP seconds the container's CPU (% of one thread), resident memory (MB, the cgroup's, what the scheduler plans
with), download and upload (MB/s; "net", "out"), with its planned memory and limit (the scheduler's), in
/stats/_usage/<job>-<start>.json, written every 30 s while it runs ("end": null) and at its end; past USAGE_POINTS
samples two neighbours merge (the step doubles: CPU and the network averaged, memory the higher).

    python /code/runner/measure.py <job> -- <command ...>   (the repo's copy: no image rebuild for a change here)
"""
import json
import os
import resource
import subprocess
import sys
import threading
import time

QUIET = ("already published", "Nothing new", "nothing new", "is published already")
USAGE_STEP, USAGE_POINTS = 5, 240


def cgroup_now():
    """The container's own CPU time (ns) and resident memory (bytes), cgroups v1 (the Tower) or v2; None unreadable."""
    for cpu_path, cpu_key, mem_path, mem_key in (
            ("/sys/fs/cgroup/cpuacct/cpuacct.usage", None, "/sys/fs/cgroup/memory/memory.stat", "total_rss"),
            ("/sys/fs/cgroup/cpu.stat", "usage_usec", "/sys/fs/cgroup/memory.stat", "anon")):
        try:
            with open(cpu_path) as f:
                text = f.read()
            cpu = int(text) if cpu_key is None else \
                int(next(line.split()[1] for line in text.splitlines() if line.startswith(cpu_key + " "))) * 1000
            with open(mem_path) as f:
                mem = int(next(line.split()[1] for line in f if line.startswith(mem_key + " ")))
            return cpu, mem
        except (OSError, StopIteration, ValueError):
            continue
    return None


class Usage:
    """The run's CPU, memory, download and upload every USAGE_STEP s (the module's note)."""

    def __init__(self, job, start):
        self.path = f"/stats/_usage/{job}-{round(start)}.json"
        # Its planned memory and container limit (MB, the scheduler's MG_PLAN_MB, MG_LIMIT_MB): the card's dashed lines.
        plan, limit = (os.environ.get(k) for k in ("MG_PLAN_MB", "MG_LIMIT_MB"))
        self.head = {"job": job, "start": round(start), "plan_mb": int(plan) if plan and plan.isdigit() else None,
                     "limit_mb": int(limit) if limit and limit.isdigit() else None}
        self.step, self.cpu, self.mem, self.net, self.out = USAGE_STEP, [], [], [], []
        self.last, self.acc = None, []  # the previous reading; the raw samples not yet a point (a step over USAGE_STEP)

    def sample(self):
        now, cg, (rx, tx) = time.time(), cgroup_now(), net_bytes()
        if cg is None:
            return
        if self.last:
            t0, cpu0, rx0, tx0 = self.last
            dt = max(now - t0, 0.1)
            self.acc.append(((cg[0] - cpu0) / dt / 1e7, cg[1] / 2**20, max(rx - rx0, 0) / dt / 1e6,
                             max(tx - tx0, 0) / dt / 1e6))
            if len(self.acc) >= self.step // USAGE_STEP:
                n = len(self.acc)
                self.cpu.append(round(sum(a[0] for a in self.acc) / n))
                self.mem.append(round(max(a[1] for a in self.acc)))
                self.net.append(round(sum(a[2] for a in self.acc) / n, 2))
                self.out.append(round(sum(a[3] for a in self.acc) / n, 2))
                self.acc = []
            if len(self.cpu) >= USAGE_POINTS:  # an even count: each merged point two whole steps
                pairs = lambda v: [v[i:i + 2] for i in range(0, len(v), 2)]
                mean = lambda v, digits: [round(sum(p) / len(p), digits) for p in pairs(v)]
                self.cpu, self.mem = [round(sum(p) / len(p)) for p in pairs(self.cpu)], [max(p) for p in pairs(self.mem)]
                self.net, self.out = mean(self.net, 2), mean(self.out, 2)
                self.step *= 2
        self.last = (now, cg[0], rx, tx)

    def peak(self):
        """The container's most resident memory (MB) in the samples so far, 0 without any."""
        return max(self.mem + [a[1] for a in self.acc], default=0)

    def write(self, end=None, peak_mb=None):
        try:
            os.makedirs("/stats/_usage", exist_ok=True)
            with open(self.path + ".tmp", "w") as f:
                json.dump(dict(self.head, end=round(end) if end else None, step=self.step, peak_mb=peak_mb,
                               cpu=self.cpu, mem=self.mem, net=self.net, out=self.out), f, separators=(",", ":"))
            os.replace(self.path + ".tmp", self.path)
        except OSError:
            pass


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
    rx0, tx0 = net_bytes()
    start = time.time()
    use = Usage(job, start)
    use.sample()

    def watch():
        tick = 0
        while not done.wait(USAGE_STEP):
            tick += 1
            use.sample()
            if tick % 2 == 0:
                peak[0] = max(peak[0], folder_bytes("/work"))
            if tick % 6 == 0:
                use.write()
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()

    tail = []
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                               env=dict(os.environ, PYTHONUNBUFFERED="1", TMPDIR="/work/tmp"))
    # Each line on at once (23.10.2026: to a file our stdout is block-buffered, so a run's log stayed empty until its
    # end: World ECMWF's 72 minutes).
    for line in process.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
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
        # The biggest process's peak or the container's sampled one, whichever is higher (23.10.2026: ru_maxrss is one
        # process's, so a job on several processes, ECMWF's --workers, would be planned at a third of its memory).
        "peak_rss_mb": max(round(usage.ru_maxrss / 1024), use.peak()),
        "net_in_mb": round((rx1 - rx0) / 1e6, 1), "net_out_mb": round((tx1 - tx0) / 1e6, 1),
        "out_mb": round(out / 1e6, 1), "disk_peak_mb": round(max(peak[0], folder_bytes("/work")) / 1e6),
        "exit": code, "worked": not any(q in line for line in tail for q in QUIET), "tail": tail[-3:],
    }
    with open(f"/stats/{job}.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")
    watcher.join(30)
    use.write(end, record["peak_rss_mb"])
    sys.exit(code)


if __name__ == "__main__":
    main()
