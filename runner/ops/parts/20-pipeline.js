// MARK: Pipeline

// Where each map's runs come from: [Open-Meteo's model (its open data's record says when a run became complete: the
// run is "available" to us then), GitHub's workflow, the world.yml title's model word]. The owner, 21.10.2026: "our
// time" is from a model update being available to riders seeing it, sooner is better; mockup
// docs/mockups/pipeline-timeline-2026-10-21.html in the app's repo.
const SET_SRC = { metnordic: ["metno_nordic_pp", "regional.yml"], "metnordic-wx": ["metno_nordic_pp", "regional.yml"],
  arome: ["meteofrance_arome_france_hd", "regional.yml"], "icon-ch1": ["meteoswiss_icon_ch1", "regional.yml"],
  "harm-nl": ["knmi_harmonie_arome_netherlands", "regional.yml"], dini: ["dmi_harmonie_arome_europe", "regional.yml"],
  "icon-d2": ["dwd_icon_d2", "regional.yml"], "icon-2i": ["italia_meteo_arpae_icon_2i", "regional.yml"], "harm-fi": [null, "regional.yml"],
  "metnordic-1km": ["metno_nordic_pp", "nordic.yml"], ukmo: ["ukmo_uk_deterministic_2km", "ukmo.yml"],
  "icon-ch2": ["meteoswiss_icon_ch2", "bucket.yml"], arome25: ["meteofrance_arome_france0025", "bucket.yml"],
  "knmi-eu": ["knmi_harmonie_arome_europe", "bucket.yml"], aladin: [null, "update.yml"],
  "icon-eu": ["dwd_icon_eu", "world.yml", "icon-eu"], "icon-eu-wx": ["dwd_icon_eu", "world.yml", "icon-eu"], "icon-eu-steer": ["dwd_icon_eu", "world.yml", "icon-eu"],
  ecmwf: ["ecmwf_ifs", "world.yml", "ecmwf"], gfs: ["ncep_gfs025", "world.yml", "gfs"], icon: ["dwd_icon", "world.yml", "icon"],
  gdps: ["cmc_gem_gdps_15km", "world.yml", "gdps"], ewam: ["dwd_ewam", "world.yml", "ewam"], "ecmwf-waves": ["ecmwf_wam", "world.yml", "ecmwf-waves"],
  mix: [null, "mix.yml"], "mix-world": [null, "mix-world.yml"], "mix-wx": [null, "weather-mix.yml"], "mix-world-wx": [null, "weather-mix.yml"],
  chance: [null, "chance.yml"], sea: [null, "sea.yml"], waves: [null, "waves.yml"], water: [null, "update.yml"], "wrf-tarifa": ["ncep_gfs025", null] };
// The model each home job watches (runner/jobs.py's watch).
const JOB_WATCH = { "regional-wind": "metno_nordic_pp", "regional-weather": "metno_nordic_pp", "nordic-1km": "metno_nordic_pp",
  "regional-icon-d2": "dwd_icon_d2", "regional-dini": "dmi_harmonie_arome_europe", "regional-harm-nl": "knmi_harmonie_arome_netherlands",
  "regional-arome": "meteofrance_arome_france_hd", "regional-icon-ch1": "meteoswiss_icon_ch1", "regional-icon-2i": "italia_meteo_arpae_icon_2i",
  "icon-eu": "dwd_icon_eu", "world-gfs": "ncep_gfs025", "world-ecmwf": "ecmwf_ifs", "world-icon": "dwd_icon", "world-gdps": "cmc_gem_gdps_15km",
  "ecmwf-waves": "ecmwf_wam", ewam: "dwd_ewam", "bucket-icon-ch2": "meteoswiss_icon_ch2", "bucket-knmi-eu": "knmi_harmonie_arome_europe",
  "bucket-arome25": "meteofrance_arome_france0025", ukmo: "ukmo_uk_deterministic_2km" };
// The mixes' inputs (views.PRODUCTS, weather_mix.py EUROPE/WORLD, mix_world.py WORLD_MODELS): their "available" is
// the first input run published since the mix's previous run started (the owner, 22.10.2026: "available for the
// mixes, from when a new input model run was published").
const MIX_INPUTS = {
  mix: ["metnordic-1km", "dini", "harm-fi", "arome", "icon-d2", "icon-ch1", "harm-nl", "icon-2i", "aladin", "ukmo", "icon-ch2", "arome25",
    "knmi-eu", "icon-eu", "ecmwf", "gfs", "wrf-tarifa"],
  "mix-world": ["icon-eu", "ecmwf", "gfs", "icon", "gdps"],
  "mix-wx": ["metnordic-1km-wx", "dini-wx", "arome-wx", "icon-d2-wx", "icon-ch1-wx", "harm-nl-wx", "icon-2i-wx", "ukmo", "icon-ch2", "arome25",
    "knmi-eu", "icon-eu-wx", "ecmwf", "gfs"],
  "mix-world-wx": ["icon-eu-wx", "ecmwf", "gfs", "icon", "gdps"],
};
const GROUPS = ["Regional", "World and Europe", "Waves and seas", "Mixes and products", "Our own", "Live feeds"];
// The watched files without a lane of their own (the owner, 26.10.2026: "Radar late" in the header and nothing red in
// the table): [who makes it, its usual interval and its late age in seconds (the station server's WATCHED)]. Their
// publishes: the panel server's views.FEEDS, each file's Last-Modified every 2 minutes.
// (The satellite clouds, ALADIN's spots and the gauges at home from 26.10.2026, update.yml their backup.)
// (The radar's GeoTIFFs, the satellite clouds and the rain nowcast on the Mac mini from 26.10.2026, the Tower their
// backup: each run's bar in its machine's colour.)
const FEED_ROWS = { radar: ["Mac · Tower · Cloudflare", 300, 1800], sat: ["Mac · Tower", 900, 2700], "rain-now": ["Mac · Tower", 3600, 3 * 3600],
  ens: ["Tower", 12 * 3600, 20 * 3600], "helper-aladin": ["Tower", 6 * 3600, 14 * 3600], "helper-gauges": ["Tower", 900, 5400] };
// Each feed's job (runner/jobs.py): its runs are the lane's bars (the tiny ones, Kolka and the radar's GeoTIFFs, keep
// out of the runs' record: their lanes show the publishes only).
const FEED_JOB = { radar: "opera", sat: "clouds", "rain-now": "rain-now", ens: "ens", "helper-aladin": "aladin-spots", "helper-gauges": "gauges" };
// Every WRF of ours together (the owner, 1.11.2026: "put WRF Capetown below WRF tarifa so its in one place").
const groupOf = (set) => set.startsWith("wrf-") ? "Our own"
  : ["ewam", "ecmwf-waves", "waves", "sea", "water"].includes(set) ? "Waves and seas"
  : set.startsWith("mix") || set === "chance" ? "Mixes and products"
  : ["icon-eu", "icon-eu-wx", "icon-eu-steer", "ecmwf", "gfs", "icon", "gdps", "ukmo-global"].includes(set) ? "World and Europe" : "Regional";
const med = (a) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); return s[s.length >> 1]; };
const mins = (s) => s == null ? "–" : s >= 3600 ? (s / 3600).toFixed(1) + " h" : Math.max(0, Math.round(s / 60)) + " min";

function laneData(maps, fr, f, now, s) {
  if (!maps || !maps.maps) return [];
  const byMap = {};
  for (const r of maps.runs || []) {
    const [name, run, online, published, frames, failed] = r;
    (byMap[name] = byMap[name] || []).push({ run: +run, published: published ? +published : null, failed: +failed === 1 });
  }
  const freshBySet = {};
  for (const r of (fr && fr.rows) || []) if (r.set) freshBySet[r.set] = r;
  const homeBySet = {};
  for (const j of (f && f.jobs) || []) for (const [set, jobs] of Object.entries(SET_JOBS)) if (jobs.includes(j.job)) homeBySet[set] = j;
  const pipe = (s && s.pipe) || { arrivals: [], gh: [], wrf: [] };
  const arrBy = {};
  for (const [m, r, d] of pipe.arrivals) (arrBy[m] = arrBy[m] || {})[r] = d;
  const live = new Set((f && f.live) || []);
  const since = now - 40 * 3600, day = now - 24 * 3600;
  const labels = Object.fromEntries(maps.maps.map((m) => [m.name.replace(/^tiles:/, ""), m.label]));
  return maps.maps.map((m) => {
    const set = m.name.startsWith("tiles:") ? m.name.slice(6) : m.name;
    const [om, wf, word] = SET_SRC[set] || [];
    const wrf = set.startsWith("wrf-");
    const runs = (byMap[m.name] || []).sort((a, b) => a.run - b.run);
    const jobs = SET_JOBS[set] || [];
    // Made at home only (every job behind it live): GitHub shows only where it really stood in (the owner, 24.10.2026).
    const homeOnly = !wrf && jobs.length > 0 && jobs.every((j) => live.has(j));
    const towerRuns = ((s && s.home) || []).filter((r) => jobs.includes(r.job));
    // Made at home only, GitHub's runs that stood aside (home_live.py: done in a minute, nothing published) claim no
    // publish: one over a Tower publish read as GitHub's (25.10.2026). A run standing in for the Tower takes minutes.
    const ghRuns = wf ? pipe.gh.filter((g) => g[0] === wf && (!word || g[1] === word || g[1] === "(all)")
      && !(homeOnly && g[3] != null && g[3] - g[2] < 120)) : [];
    const avails = (om && arrBy[om]) || {};
    // Each model run: when it was available (Open-Meteo's record), GitHub's publish, the Tower's run.
    const byRun = {};
    if (wrf) {
      // WRF reads GFS from NOAA, hours before Open-Meteo's record: available is when its log had GFS in hand after
      // waiting for it, else WRF's start (GFS was out by then).
      // Each lane its own region's runs (the owner, 2.11.2026: Cape Town's lane showed only its publish).
      for (const [a, b, ok, ready, waited, cycle, phases, host, region] of pipe.wrf) if (cycle && set === "wrf-" + (region || "tarifa")) byRun[cycle] = { run: cycle, avail: waited && ready ? ready : a, early: !waited,
        tower: { start: a, end: b, ok: ok !== false, going: ok === "going", waiting: ok === "waiting", live: true, job: "wrf", host: host || "tower", phases } };
    } else for (const [r, d] of Object.entries(avails)) byRun[+r] = { run: +r, avail: d };
    for (const r of runs) if ((r.published || r.run) >= since) {
      const e = byRun[r.run] = byRun[r.run] || { run: r.run, avail: null };
      e.pub = r.published; e.failed = r.failed;
    }
    const entries = Object.values(byRun).sort((a, b) => a.run - b.run);
    const newestPub = Math.max(0, ...runs.filter((r) => r.published).map((r) => r.run));
    const matched = new Set();
    for (const e of entries) {
      // GitHub's job: its workflow's run that was going when the map was published (or is going now).
      if (ghRuns.length) {
        // The run that ended closest after the publish (its last step uploads the index): a short "nothing new" run
        // overlapping it was picked before (the Mix read 11 min on GitHub where its publishing runs took 22-51).
        const near = (g) => g[3] == null ? 1e9 : g[3] >= e.pub - 60 ? g[3] - e.pub : 1e8;
        const g = e.pub ? ghRuns.filter((g) => g[2] <= e.pub + 60 && (g[3] == null || g[3] >= e.pub - 120))
            .sort((a, b) => Math.abs(near(a)) - Math.abs(near(b)))[0]
          // A job going now is the newest available run's: an older unpublished run was passed over (MET Nordic 1 km's
          // 14Z claimed it on 22.10.2026, an 11 h "wait" and no "not taken" mark).
          : e.avail != null ? ghRuns.find((g) => g[3] == null && g[2] >= e.avail - 120
            && !entries.some((o) => o.run > e.run && o.avail != null && o.avail <= g[2] + 120)) : null;
        if (g) e.gh = { start: g[2], end: g[3] };
      }
      // The Tower's: a job that watches this model, its run made from this source run; the others ride along (the
      // regional job watches MET Nordic and makes the regional models it finds): its first good run within 3 h.
      if (!wrf && e.avail != null) {
        // (since 21.10.2026 evening each run records every watched model's run as it started: exact)
        const t = towerRuns.filter((t) => t.sources && t.sources[om] === e.run).sort((a, b) => a.start - b.start)[0]
          || towerRuns.find((t) => JOB_WATCH[t.job] === om && t.source === e.run)
          || towerRuns.filter((t) => JOB_WATCH[t.job] !== om && t.exit === 0 && !t.idle && t.start >= e.avail - 120 && t.start <= e.avail + 3 * 3600)
            .sort((a, b) => a.start - b.start)[0];
        if (t) { matched.add(t); e.tower = { start: t.start, end: t.end, due: t.due, ok: t.exit === 0, live: t.live, job: t.job, cpu: t.cpu, peak: t.peak, net: t.net,
                                             dl_s: t.dl_s, dl_mb: t.dl_mb, dl_files: t.dl_files, dl_first: t.dl_first }; }
      }
      if (homeOnly && e.gh && e.tower && e.tower.live && e.tower.ok && e.pub && Math.abs(e.pub - e.tower.end) < 600) delete e.gh;
      e.ghTime = !wrf && e.avail != null && e.pub && (e.gh || !homeOnly) ? e.pub - e.avail : null;
      e.towerTime = e.avail == null ? null : wrf ? (e.pub ? e.pub - e.avail : null) : e.tower && e.tower.ok ? e.tower.end - e.avail : null;
      e.skipped = !wrf && e.avail != null && !e.pub && !e.gh && !(e.tower && e.tower.live && e.tower.ok)
        && (now - e.avail > 3 * 3600 || newestPub > e.run);
    }
    // A mix: each run's "available" from its inputs' publishes (the first since its previous run started; none: a run
    // with nothing new, e.g. its far hours remade), for GitHub's runs and the Tower's.
    const inputs = MIX_INPUTS[set];
    let towerLoose = towerRuns, homeAvail = null;
    const running = homeBySet[set];
    if (inputs) {
      const pubs = inputs.flatMap((name) => (byMap["tiles:" + name] || []).filter((r) => r.published).map((r) => [r.published, labels[name] || name]))
        .sort((a, b) => a[0] - b[0]);
      const firstNew = (from, to) => pubs.find((p) => p[0] > from && p[0] <= to) || null;
      let prev = null;
      for (const e of entries) {
        if (!e.gh) continue;
        const p = prev == null ? null : firstNew(prev, e.gh.start);
        prev = e.gh.start;
        if (p) { e.avail = p[0]; e.availFrom = p[1]; e.ghTime = e.pub ? e.pub - p[0] : null; }
      }
      let prevT = null;
      towerLoose = [...towerRuns].sort((a, b) => a.start - b.start).map((t) => {
        const p = prevT == null ? null : firstNew(prevT, t.start);
        prevT = t.start;
        return p ? { ...t, avail: p[0], availFrom: p[1] } : t;
      });
      // The job running at home now: the same, from its last run's start (the owner, 22.10.2026: "have to have same
      // for Tower ... later we will remove GitHub").
      const p = running && running.started && prevT != null ? firstNew(prevT, running.started) : null;
      if (p) homeAvail = { avail: p[0], availFrom: p[1] };
    } else if (running && running.started && !wrf) {
      // Else the newest run available by its start that the Tower hasn't made yet (within 6 h).
      const cur = [...entries].reverse().find((o) => o.avail != null && o.avail <= running.started + 120);
      if (cur && !cur.tower && running.started - cur.avail < 6 * 3600) homeAvail = { avail: cur.avail, run: cur.run };
    }
    const recent = entries.filter((e) => (e.avail || e.pub || e.run) >= day);
    const runTimes = Object.keys(avails).map(Number).sort((a, b) => a - b);
    const cad = wrf ? (set === "wrf-tarifa" ? 12 : 24) * 3600 : runTimes.length > 1 ? med(runTimes.slice(1).map((r, i) => r - runTimes[i])) : null;
    const lastAvail = runTimes.length ? avails[runTimes[runTimes.length - 1]] : null;
    const last = runs[runs.length - 1];
    // Without a source's rhythm (a job on a clock: chance of rain, the seas): the Tower's own planned start, the
    // scheduler's "next" (the owner, 22.10.2026: chance of rain looked unscheduled; a guess from its last run fell in
    // the past and drew nothing).
    const towerNext = ((f && f.next) || []).filter((n) => jobs.includes(n.job) && n.at).map((n) => n.at).sort((a, b) => a - b)[0] || null;
    // Cape Town has no clock of its own: it follows the evening Tarifa.
    const next = wrf ? (set === "wrf-tarifa" && ((f && f.wrf && f.wrf.next) || [])[0]) || null
      : cad && lastAvail ? lastAvail + cad : towerNext || (last ? last.run + (m.interval + m.delay) * 3600 : null);
    const fresh = freshBySet[set];
    let status = "ok";
    if (fresh) status = fresh.status;
    else if (last && now - (runs.filter((r) => r.published).pop() || last).run > (m.interval + m.delay) * 3600 + 3600) status = "bad";
    // Without an "available" moment (our own products, sources Open-Meteo doesn't have): the jobs' own minutes.
    const procOnly = !wrf && !runTimes.length && !inputs;
    return { ...m, set, om, wf, wrf, group: groupOf(set), entries, jobs, towerRuns, fresh, status, cad, next, procOnly,
      loose: inputs ? towerLoose : procOnly ? towerRuns : [], home: running, homeAvail, isLive: wrf || jobs.some((j) => live.has(j)), homeOnly,
      towerT: inputs ? med(towerLoose.filter((t) => t.avail && t.exit === 0 && t.end >= day).map((t) => t.end - t.avail))
        : procOnly ? med(towerRuns.filter((t) => t.exit === 0 && t.end >= day).map((t) => t.end - t.start)) : med(recent.filter((e) => !(e.tower && e.tower.host === "mac")).map((e) => e.towerTime).filter((v) => v != null)),
      macT: med(recent.filter((e) => e.tower && e.tower.host === "mac").map((e) => e.towerTime).filter((v) => v != null)),
      ghT: procOnly ? med(recent.filter((e) => e.gh && e.pub).map((e) => e.pub - e.gh.start)) : med(recent.map((e) => e.ghTime).filter((v) => v != null)),
      offered: recent.filter((e) => e.avail != null).length, skipped: recent.filter((e) => e.skipped).length };
  });
}

// The machine band's reasons (the scheduler's queue record, runner/scheduler.py why_kind).
const WHY = { slots: "all slots busy", priority3: "priority 3 (a slot kept free)", memory: "memory", behind: "behind one waiting for memory",
  drain: "held for one waiting long", wrf: "too big beside WRF", planned: "the jobs' plans over their cap", heavy: "big jobs at their limit", starting: "starting",
  "?": "not recorded" };
const BAND_H = 246, MAC_H = 120;

// The Mac mini's band (the owner, 23.10.2026; the server's band.mac from its beats): the Tower's band's look, slimmer.
function macBandLayer(mac, x, t0, X0, X1, compact, y0) {
  const step = 120, rows = mac.rows.filter((r) => r[0] + step >= t0);
  if (!rows.length) return null;
  const CPU = [y0 + 8, y0 + 58], MEM = [y0 + 68, y0 + 104];
  const yCpu = (p) => CPU[1] - Math.min(p, 100) / 100 * (CPU[1] - CPU[0]);
  const yMem = (g) => MEM[1] - Math.min(g, mac.total_gb) / mac.total_gb * (MEM[1] - MEM[0]);
  const xm = (r) => x(r[0] + step / 2);
  // A hole in the beats (over 10 minutes) stays a hole, not a straight line across it (the owner, 28.10.2026: "its
  // showed incorrect usage", a night with no beats kept drawn as one slope from WRF's 60% down to the morning).
  const runs = [];
  rows.forEach((r, i) => (i && r[0] - rows[i - 1][0] <= 600 ? runs[runs.length - 1].push(r) : runs.push([r])));
  const line = (f) => runs.map((rs) => "M" + rs.map((r) => `${xm(r)},${f(r)}`).join("L")).join("");
  const area = (f0, f1) => runs.map((rs) => "M" + rs.map((r) => `${xm(r)},${f1(r)}`).join("L") + "L" + [...rs].reverse().map((r) => `${xm(r)},${f0(r)}`).join("L") + "Z").join("");
  const avg = rows.reduce((a, r) => a + r[1] + r[2], 0) / rows.length, peak = Math.max(...rows.map((r) => r[3]));
  return html`<g aria-label="The Mac mini">
    <path d=${area(() => yCpu(0), (r) => yCpu(r[2]))} fill="var(--wrf-soft)"/>
    <path d=${area((r) => yCpu(r[2]), (r) => yCpu(r[1] + r[2]))} fill="var(--mac-soft)"/>
    <path d=${line((r) => yCpu(r[1] + r[2]))} fill="none" stroke="var(--mac)" stroke-width="1.3"/>
    ${[50, 100].map((p) => html`<line x1=${X0} x2=${X1} y1=${yCpu(p)} y2=${yCpu(p)} stroke="var(--line)" stroke-dasharray="3 3"/>${compact ? null : html`<text x=${X0 - 6} y=${yCpu(p) + 4} text-anchor="end">${p}%</text>`}`)}
    <path d=${line((r) => yMem(r[3]))} fill="none" stroke="var(--accent)" stroke-width="1.5"/>
    ${compact ? html`<text class="cap" x=${X0 + 2} y=${CPU[0] + 11}><tspan class="grp" style="fill:var(--mac)">MAC MINI </tspan><tspan style="fill:var(--ink)">CPU ${mac.threads} cores · avg ${Math.round(avg)}%</tspan></text>
        <text class="cap" x=${X0 + 2} y=${MEM[0] + 11}><tspan class="grp" style="fill:var(--muted)">MEMORY </tspan><tspan style="fill:var(--ink)">peak ${peak.toFixed(1)} GB</tspan></text>`
      : html`<text class="grp" x="0" y=${CPU[0] + 12} style="fill:var(--mac)">MAC MINI</text><text x="0" y=${CPU[0] + 26}>CPU · ${mac.threads} cores</text>
        <text class="grp" x="0" y=${MEM[0] + 12}>MEMORY</text><text x="0" y=${MEM[0] + 26}>used</text>
        <text x=${X0 - 6} y=${MEM[0] + 8} text-anchor="end">${Math.round(mac.total_gb)} GB</text>
        <text class="side" x=${X1 + 10} y=${CPU[0] + 14}>avg ${Math.round(avg)}%</text>
        <text class="side" x=${X1 + 10} y=${MEM[0] + 14}>peak ${peak.toFixed(1)} GB</text>`}
    <line x1="0" x2=${compact ? X1 + 6 : X1 + 190} y1=${y0 + 112} y2=${y0 + 112} stroke="var(--line)"/>
  </g>`;
}

// The machine on top of the lanes (the owner, 22.10.2026: "on top we could add a total PC usage graph"; mockup
// pipeline-machine-band-2026-10-22): CPU (WRF, jobs), jobs running and waiting against the slots, memory against the
// jobs' budget, red where jobs waited while the machine had room (CPU under 50%, over 6 GB free).
const roomy = (r, total) => r[5] > 0 && r[1] + r[2] < 50 && total - r[3] > 6;
function bandLayer(band, x, t0, slots, X0, X1, compact) {
  const top = 24, step = band.step || 120, total = band.total_gb || 25;
  const rows = band.rows.filter((r) => r[0] + step >= t0);
  if (!rows.length) return null;
  const CPU = [top, top + 56], JOBS = [top + 66, top + 122], MEM = [top + 132, top + 172], NET = [top + 182, top + 222];
  const yCpu = (p) => CPU[1] - Math.min(p, 100) / 100 * (CPU[1] - CPU[0]);
  // The network (the owner, 22.10.2026: "on top lets show bandwidth utilization too"): in as an area, out as a line,
  // MB/s, scaled to the peak (at least 5 MB/s); rows from before the scheduler's network samples have none.
  const netRows = rows.filter((r) => r[10] != null);
  const netPeak = Math.max(5, ...netRows.map((r) => Math.max(r[10], r[11] || 0)));
  const yNet = (v) => NET[1] - Math.min(v, netPeak) / netPeak * (NET[1] - NET[0]);
  const netAvg = netRows.length ? netRows.reduce((s, r) => s + r[10], 0) / netRows.length : null;
  const maxJobs = Math.max(8, ...rows.map((r) => r[4] + r[5]));
  const yJobs = (n) => JOBS[1] - n / maxJobs * (JOBS[1] - JOBS[0]);
  const yMem = (g) => MEM[1] - Math.min(g, total) / total * (MEM[1] - MEM[0]);
  const xm = (r) => x(r[0] + step / 2);
  const spans = (test) => {
    const out = [];
    let start = null;
    rows.forEach((r, i) => {
      if (test(r) && start == null) start = r[0];
      const end = !test(r) || i === rows.length - 1;
      if (start != null && end) { out.push([start, test(r) ? r[0] + step : r[0]]); start = null; }
    });
    return out;
  };
  const area = (f0, f1) => "M" + rows.map((r) => `${xm(r)},${f1(r)}`).join("L") + "L" + [...rows].reverse().map((r) => `${xm(r)},${f0(r)}`).join("L") + "Z";
  const room = rows.filter((r) => roomy(r, total));
  const avg = rows.reduce((a, r) => a + r[1] + r[2], 0) / rows.length;
  return html`<g aria-label="The machine">
    ${spans((r) => roomy(r, total)).map(([a, b]) => html`<rect x=${x(a)} y=${CPU[0]} width=${Math.max(x(b) - x(a), 1)} height=${MEM[1] - CPU[0]} fill="var(--room)"/>`)}
    ${spans((r) => r[6]).map(([a, b]) => html`<rect x=${x(a)} y=${CPU[0] - 5} width=${Math.max(x(b) - x(a), 1)} height="3" fill="var(--wrf)"/>`)}
    <path d=${area(() => yCpu(0), (r) => yCpu(r[2]))} fill="var(--wrf-soft)"/>
    <path d=${area((r) => yCpu(r[2]), (r) => yCpu(r[1] + r[2]))} fill="var(--home-soft)"/>
    <path d=${"M" + rows.map((r) => `${xm(r)},${yCpu(r[1] + r[2])}`).join("L")} fill="none" stroke="var(--home)" stroke-width="1.3"/>
    ${[50, 100].map((p) => html`<line x1=${X0} x2=${X1} y1=${yCpu(p)} y2=${yCpu(p)} stroke="var(--line)" stroke-dasharray="3 3"/>${compact ? null : html`<text x=${X0 - 6} y=${yCpu(p) + 4} text-anchor="end">${p}%</text>`}`)}
    ${rows.map((r) => { const a = x(r[0]), w = Math.max(x(r[0] + step) - a - 0.4, 0.8);
      return html`${r[4] ? html`<rect x=${a} y=${yJobs(r[4])} width=${w} height=${JOBS[1] - yJobs(r[4])} fill="var(--home)" opacity=".7"/>` : null}
        ${r[5] ? html`<rect x=${a} y=${yJobs(r[4] + r[5])} width=${w} height=${yJobs(r[4]) - yJobs(r[4] + r[5])} fill="var(--wait)"/>` : null}`; })}
    <line x1=${X0} x2=${X1} y1=${yJobs(slots)} y2=${yJobs(slots)} stroke="var(--ink)" stroke-dasharray="5 4" stroke-width="1.2"/>
    <path d=${"M" + rows.map((r) => `${xm(r)},${yMem(r[3])}`).join("L")} fill="none" stroke="var(--accent)" stroke-width="1.5"/>
    <path d=${"M" + rows.map((r) => `${x(r[0])},${yMem(r[9])}L${x(r[0] + step)},${yMem(r[9])}`).join("L")} fill="none" stroke="var(--accent)" stroke-dasharray="4 3"/>
    ${compact ? null : html`<text x=${X0 - 6} y=${yJobs(slots) + 4} text-anchor="end">${slots} slots</text><text x=${X0 - 6} y=${JOBS[0] + 8} text-anchor="end">${maxJobs}</text>
    <text x=${X0 - 6} y=${MEM[0] + 8} text-anchor="end">${Math.round(total)} GB</text>
    <text class="grp" x="0" y=${CPU[0] + 12}>CPU</text><text x="0" y=${CPU[0] + 26}>${band.threads} threads</text>
    <text class="grp" x="0" y=${JOBS[0] + 12}>JOBS</text><text x="0" y=${JOBS[0] + 26}>running · waiting</text>
    <text class="grp" x="0" y=${MEM[0] + 12}>MEMORY</text><text x="0" y=${MEM[0] + 26}>used · budget</text>
    <text class="side" x=${X1 + 10} y=${CPU[0] + 14}>avg ${Math.round(avg)}%</text>
    <text class="side" x=${X1 + 10} y=${JOBS[0] + 14} style=${room.length ? "fill:var(--bad)" : ""}>${room.length * Math.round(step / 60)} min</text><text class="side" x=${X1 + 10} y=${JOBS[0] + 28}>waited with room</text>
    <text class="side" x=${X1 + 10} y=${MEM[0] + 14}>peak ${Math.max(...rows.map((r) => r[3])).toFixed(1)} GB</text>`}
    ${netRows.length ? html`<path d=${"M" + netRows.map((r) => `${xm(r)},${yNet(0)}L${xm(r)},${yNet(r[10])}`).join("M")} stroke="var(--net-soft)" stroke-width=${Math.max(x(netRows[0][0] + step) - x(netRows[0][0]) - 0.4, 0.8)}/>
      <path d=${"M" + netRows.map((r) => `${xm(r)},${yNet(r[10])}`).join("L")} fill="none" stroke="var(--net)" stroke-width="1.4"/>
      <path d=${"M" + netRows.map((r) => `${xm(r)},${yNet(r[11] || 0)}`).join("L")} fill="none" stroke="var(--net)" stroke-width="1" stroke-dasharray="3 3"/>` : null}
    ${compact ? (() => {
      // A phone (the owner, 22.10.2026: "on my phone not looking good"): each row's caption in its top-left, its scale
      // at the right end, both with a halo so they read over the areas.
      const cap = (y, title, rest, bad) => html`<text class="cap" x=${X0 + 2} y=${y + 11}><tspan class="grp" style="fill:var(--muted)">${title + " "}</tspan>
        <tspan style=${bad ? "fill:var(--bad)" : "fill:var(--ink)"}>${rest}</tspan></text>`;
      const scale = (y, t) => html`<text class="cap" x=${X1 - 2} y=${y + 11} text-anchor="end">${t}</text>`;
      return html`${cap(CPU[0], "CPU", `${band.threads} threads · avg ${Math.round(avg)}%`)}${scale(CPU[0], "100%")}
        ${cap(JOBS[0], "JOBS", room.length ? `${room.length * Math.round(step / 60)} min waited with room` : "running · waiting", room.length)}${scale(JOBS[0], `${maxJobs} · ${slots} slots`)}
        ${cap(MEM[0], "MEMORY", `peak ${Math.max(...rows.map((r) => r[3])).toFixed(1)} GB`)}${scale(MEM[0], `${Math.round(total)} GB`)}
        ${cap(NET[0], "NETWORK", netAvg != null ? `avg ${netAvg.toFixed(1)} · peak ${Math.max(...netRows.map((r) => r[10])).toFixed(0)} MB/s` : "from 22.10")}${scale(NET[0], `${Math.round(netPeak)} MB/s`)}`;
    })() : html`<text x=${X0 - 6} y=${NET[0] + 8} text-anchor="end">${Math.round(netPeak)} MB/s</text>
    <text class="grp" x="0" y=${NET[0] + 12}>NETWORK</text><text x="0" y=${NET[0] + 26}>in · out (dashed)</text>
    <text class="side" x=${X1 + 10} y=${NET[0] + 14}>${netAvg != null ? `avg ${netAvg.toFixed(1)} MB/s` : "from 22.10"}</text>
    ${netRows.length ? html`<text class="side" x=${X1 + 10} y=${NET[0] + 28}>peak ${Math.max(...netRows.map((r) => r[10])).toFixed(0)} MB/s in</text>` : null}`}
    <line x1="0" x2=${compact ? X1 + 6 : X1 + 190} y1=${top + 232} y2=${top + 232} stroke="var(--line)"/>
  </g>`;
}

function BandTip({ band, row, left, top }) {
  const total = band.total_gb || 25, free = total - row[3];
  const who = row[8].map((i) => band.names[i]);
  const why = Object.entries(row[7] || {}).map(([k, n]) => `${WHY[k] || k} ${n}`).join(" · ");
  return html`<div class="btip" style=${`left:${left}px;top:${top}px`}>
    <b>${clock(row[0])} ${zoneName()}</b>${row[6] ? " · WRF running" : ""}<br/>
    <span class="k">CPU</span> ${Math.round(row[1] + row[2])}% (jobs ${Math.round(row[1])}, WRF ${Math.round(row[2])})<br/>
    <span class="k">memory</span> ${row[3].toFixed(1)} of ${total} GB, ${free.toFixed(1)} free · jobs' budget ${row[9]}<br/>
    ${row[10] != null ? html`<span class="k">network</span> ${row[10]} MB/s in, ${row[11] ?? 0} out<br/>` : null}
    <span class="k">jobs</span> ${row[4]} running, ${row[5]} waiting${who.length ? ": " + who.slice(0, 8).join(", ") + (who.length > 8 ? "…" : "") : ""}<br/>
    ${row[5] ? html`<span class="k">why</span> ${why}<br/>` : null}
    ${roomy(row, total) ? html`<span class="room">waiting with room: the machine wasn't full</span>` : null}
  </div>`;
}

// Where a home run's wait (amber) starts: the scheduler holding it from due to start, and none before a run that found
// nothing new (its record's idle, scheduler.made_nothing). The mixes' own pace (the Mix makes each hour once, the world
// mix every 5 h) is no wait: an input published meanwhile shows in "ours", not as amber (the owner, 29.10.2026: "there
// still are some long waitings", most were re-checks and the mixes' pace; "Yes, A then B").
function held(r, start) {
  return r.idle || !r.due || start - r.due <= 60 ? null : r.due;
}

// The download part of a home run's bar (the owner, 22.10.2026: "make also on bar download indication"): from its first
// download for its downloads' busy time, within [from, to] (most jobs download, then make; one that alternates shows
// its total there). A finished run's record (dl_first, dl_s) or a running job's (dl: first, busy_s).
function dlPart(r, from, to) {
  const first = r.dl ? r.dl.first : r.dl_first, busy = r.dl ? r.dl.busy_s : r.dl_s;
  if (!busy || busy < 5) return null;
  const a = Math.max(first || from, from), b = Math.min(a + busy, to);
  return b > a ? [a, b] : null;
}

// A running job's expected end from its usual time (the median of its last 10 runs); WRF's from its model time.
function jobEnd(j, now) {
  const typ = j && j.typical ? j.typical * 60 : null;
  if (!j || !j.started || !typ) return null;
  const end = j.started + typ;
  return { end, typ, pct: Math.min((now - j.started) / typ, 1), late: now > end };
}
function wrfEnd(w, now) {
  if (!w || !w.running || w.progress == null || w.progress < 0.03) return null;
  const from = w.computing_from || w.started;
  return { end: from + (now - from) / w.progress, pct: w.progress, from, late: false };
}

// A home run's span on the timeline: [from (available, else due, else its start), start, end (a running one: now)].
function homeSpan(d, now) {
  const running = d.kind === "running", r = running ? d.j : d.t, a = (d.e || {}).avail;
  if (!r) return null;
  const start = r.started || r.start, due = r.due && r.due > 1e9 ? r.due : null;
  const from = a != null && a <= start + 60 ? a : r.avail || due || start;
  return [Math.min(from, start), start, running ? now : r.end];
}

// The short names of the scheduler's reasons, for the timeline's amber part when the long one doesn't fit.
const WHY_SHORT = { slots: "slots full", priority3: "priority 3", memory: "memory", behind: "behind one", drain: "held",
  wrf: "beside WRF", planned: "plans full", heavy: "big jobs", starting: "starting", "?": "?" };

// One run on a timeline (RunTimeline): the card's body for every home run (the owner, 23.10.2026: "lets make better card style. We
// can make it as timeline and all data show as graph"; "Build variant A: the job on its own scale plus the machine
// row"; 25.10.2026: "card same as for other tasks same for tasks on mac mini", "on hover we show additional info same
// as on machine header. So we can see details on graph"; mockup docs/mockups/ops-card-timeline-2026-10-23 in the app's
// repo). One time axis from spec.t0 to spec.tEnd (a running one striped to its expected end); the steps as marks; a
// strip of the run's parts (spec.segs(u): [from, to, fill, [texts, the first that fits], what it was]), the moments it
// downloaded in teal; GitHub's run to its publish; then the run's own CPU, memory and network on its own scale (u from
// /api/usage?<q>: measure.py's 5 s samples, or WRF's from the Mac's beats / the Tower's minute samples; a gap where
// there's no sample) and one row of the whole machine that ran it (spec.machine: [t, CPU %, memory GB] rows), with one
// line of totals (spec.foot(u)). Pinned (a click), a crosshair over it says what was at that moment, like the band's
// tip. Fetched as the card opens, again every 15 s while the run goes.
const usageCache = new Map();
function RunTimeline({ q, id, running, now, spec, compact, pinned }) {
  const [u, setU] = useState(() => usageCache.get(id));
  const [hv, setHv] = useState(null);
  const svgRef = useRef(null);
  useEffect(() => {
    if (!q) return;
    let gone = false;
    const get = () => fetch(`/api/usage?${q}`, { cache: "no-store" }).then((x) => x.json())
      .then((v) => { if (gone) return; v = v || {}; if (!running) usageCache.set(id, v); setU(v); }).catch(() => {});
    if (usageCache.has(id)) setU(usageCache.get(id)); else get();
    const timer = running ? setInterval(get, 15000) : null;
    return () => { gone = true; if (timer) clearInterval(timer); };
  }, [id, running]);
  useEffect(() => { if (!pinned) setHv(null); }, [pinned]);
  const vals = (v) => (v || []).filter((a1) => a1 != null);
  const pk = (v) => vals(v).length ? Math.max(...vals(v)) : 0;
  const avg = (v) => vals(v).length ? vals(v).reduce((s1, a1) => s1 + a1, 0) / vals(v).length : 0;
  const t0 = spec.t0, gh = spec.gh || {}, pub = gh.pub;
  const tEnd = Math.max(spec.tEnd, spec.rest ? spec.rest[1] : 0);
  // GitHub's publish on the axis when it isn't far past ours (else its time at the right edge).
  const ghIn = pub && pub <= tEnd + Math.max(tEnd - t0, 600) * 0.3;
  const t1 = Math.max(tEnd, ghIn ? pub : 0, t0 + 60);
  const W = compact ? 356 : 536, X0 = compact ? 0 : 104, X1 = W - 2;
  const x = (t) => X0 + (Math.min(Math.max(t, t0), t1) - t0) / (t1 - t0) * (X1 - X0);
  const tw = (text) => text.length * 6.6;
  const span = t1 - t0, tick = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800].find((v) => span / v <= 6) || 21600;
  const ticks = [];
  for (let t = Math.ceil(t0 / tick) * tick; t <= t1; t += tick) ticks.push(t);
  const parts = [];
  let y = 18;
  // The steps as marks; the last label always shows, one that would run into the one before it or into the last is
  // left out (its mark stays).
  const marks = spec.marks.filter((m) => m && m[0]);
  let right = -1e9;
  const lastLeft = marks.length ? x(marks[marks.length - 1][0]) + 3 - tw(marks[marks.length - 1][1]) : 0;
  const shown = marks.map(([t, text, c], i) => {
    const last = i === marks.length - 1, first = i === 0;
    const anchor = first ? "start" : last ? "end" : "middle";
    const l = anchor === "start" ? x(t) - 3 : anchor === "end" ? x(t) + 3 - tw(text) : x(t) - tw(text) / 2;
    const tri = html`<path d=${`M${x(t)} ${y + 13} l-4 -6 h8 z`} fill=${c}/>`;
    if (!last && (l < right + 8 || l + tw(text) > lastLeft - 8)) return tri;
    right = l + tw(text);
    return html`${tri}<text x=${anchor === "start" ? x(t) - 3 : anchor === "end" ? x(t) + 3 : x(t)} y=${y + 3} text-anchor=${anchor} style=${`fill:${c}`}>${text}</text>`;
  });
  y += 18;
  // The run's parts, each text the first of its alternatives that fits; then the moments it downloaded, the rest.
  const SH = 14, stripY = y;
  const segs = spec.segs(u).filter((g) => g && g[1] - g[0] > 1);
  parts.push(html`${segs.map(([a1, b1, fill]) => html`<rect x=${x(a1)} y=${y} width=${Math.max(x(b1) - x(a1), 1.5)} height=${SH} fill=${fill}/>`)}
    ${(spec.dl ? spec.dl(u) : []).map(([a1, b1]) => html`<rect x=${x(a1)} y=${y} width=${Math.max(x(b1) - x(a1), 2)} height=${SH} fill="var(--accent)"/>`)}
    ${spec.rest ? html`<rect x=${x(spec.rest[0])} y=${y} width=${Math.max(x(spec.rest[1]) - x(spec.rest[0]), 1.5)} height=${SH} fill="url(#tl-rest)"/>` : null}
    ${segs.map(([a1, b1, , texts]) => { const text = (texts || []).find((t) => t && x(b1) - x(a1) > tw(t) + 10);
      return text ? html`<text class="cap ink" x=${(x(a1) + x(b1)) / 2} y=${y + 11} text-anchor="middle">${text}</text>` : null; })}
    ${compact ? null : html`<text x="0" y=${y + 11}>${spec.stripLabel || "our run"}</text>`}`);
  y += SH + 8;
  // GitHub's run: from its start (or the axis' start) to its publish; past the axis, its time at the right edge.
  if (pub || gh.start) {
    const ga = Math.max(gh.start || t0, t0), gb = pub ? Math.min(pub, t1) : now;
    parts.push(html`<rect x=${x(ga)} y=${y + 2} width=${Math.max(x(gb) - x(ga), 2)} height="6" rx="3" fill="var(--gh)" opacity=".55"/>
      ${pub && ghIn ? html`<path d=${`M${x(pub)} ${y + 1} l4 4 -4 4 -4 -4z`} fill="var(--gh)"/>` : pub ? html`<text x=${X1} y=${y + 9} text-anchor="end" class="cap">→ ${clock(pub)}</text>` : null}
      <text x=${compact ? X0 + 2 : 0} y=${y + 9} class=${compact ? "cap" : ""}>${pub ? `GitHub ${clock(pub)}` : "GitHub running"}</text>`);
    y += 18;
  }
  y += 14;
  // The rows: title, unit, the run's numbers at the left (a phone: in the row); the scale above the row's right end,
  // clear of its lines (a plan's dashed line ran through it at the top).
  const RH = 32, rows = [];
  const row = (title, sub, stat, top, draw) => {
    rows.push([y, y + RH]);
    parts.push(html`${draw(y, y + RH)}<line x1=${X0} x2=${X1} y1=${y + RH} y2=${y + RH} stroke="var(--line)"/>
      ${compact ? html`<text class="cap" x=${X0 + 2} y=${y + 10}><tspan class="grp">${title + " "}</tspan><tspan class="ink">${stat}</tspan></text>`
        : html`<text class="grp" x="0" y=${y + 9}>${title}</text><text x="0" y=${y + 21}>${sub}</text><text class="ink" x="0" y=${y + 33}>${stat}</text>`}
      <text class="cap" x=${X1 - 2} y=${y - 3} text-anchor="end">${top}</text>`);
    y += RH + 16;
  };
  const nice = (v, steps) => steps.find((s1) => s1 >= v) || Math.ceil(v);
  const ti = (i) => u.start + (i + 0.5) * u.step;
  // A series in pieces between its gaps (None: no sample in that step).
  const pieces = (v) => { const out = []; let cur = null;
    v.forEach((a1, i) => { if (a1 == null) { cur = null; return; } if (!cur) out.push(cur = []); cur.push([i, a1]); }); return out; };
  const path = (pc, f) => pc.map(([i, a1], k) => `${k ? "L" : "M"}${x(ti(i)).toFixed(1)},${f(a1).toFixed(1)}`).join("");
  const line = (v, f) => pieces(v).map((pc) => path(pc, f)).join("");
  const area = (v, f, base) => pieces(v).map((pc) => `${path(pc, f)}L${x(ti(pc[pc.length - 1][0])).toFixed(1)},${base}L${x(ti(pc[0][0])).toFixed(1)},${base}Z`).join("");
  const has = (k) => u && vals(u[k]).length >= 2;
  const cpu = has("cpu") ? u.cpu.map((c) => c == null ? null : c / 100) : null;
  const mem = has("mem") ? u.mem.map((m) => m == null ? null : m / 1000) : null;
  const net = has("net") ? u.net : null, out = net && has("out") ? u.out : null;
  const plan = u && u.plan_mb ? u.plan_mb / 1000 : null;
  if (cpu) {
    const cTop = Math.max(1, Math.ceil(pk(cpu)));
    row("CPU", "threads", `avg ${avg(cpu).toFixed(1)} · peak ${pk(cpu).toFixed(1)}`, `${cTop} thr`, (a1, b1) => {
      const f = (v) => b1 - Math.min(v, cTop) / cTop * (b1 - a1);
      return html`<path d=${area(cpu, f, b1)} fill="var(--home-soft)"/><path d=${line(cpu, f)} fill="none" stroke="var(--home)" stroke-width="1.4" stroke-linejoin="round"/>`; });
  }
  if (mem) {
    const mTop = nice(Math.max(pk(mem), plan || 0) * 1.05, [0.5, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 25, 32, 48, 64]);
    row("MEMORY", plan ? "GB · plan dashed" : "GB", plan ? `peak ${pk(mem).toFixed(1)} of ${plan.toFixed(1)}` : `peak ${pk(mem).toFixed(1)} GB`, `${mTop} GB`, (a1, b1) => {
      const f = (v) => b1 - Math.min(v, mTop) / mTop * (b1 - a1);
      return html`<path d=${area(mem, f, b1)} fill="var(--accent-soft)"/><path d=${line(mem, f)} fill="none" stroke="var(--accent)" stroke-width="1.5" stroke-linejoin="round"/>
        ${plan ? html`<line x1=${x(u.start)} x2=${x(u.start + u.mem.length * u.step)} y1=${f(plan)} y2=${f(plan)} stroke="var(--accent)" stroke-dasharray="4 3"/>` : null}`; });
  }
  if (net) {
    const nTop = nice(Math.max(pk(net), out ? pk(out) : 0) * 1.05, [1, 2, 5, 10, 20, 50, 100, 200, 500]);
    row("NETWORK", out ? "MB/s · out dashed" : "MB/s in", `avg ${MBs(avg(net))} · peak ${MBs(pk(net))}`, `${nTop} MB/s`, (a1, b1) => {
      const f = (v) => b1 - Math.min(v, nTop) / nTop * (b1 - a1);
      return html`<path d=${area(net, f, b1)} fill="var(--net-soft)"/><path d=${line(net, f)} fill="none" stroke="var(--net)" stroke-width="1.3" stroke-linejoin="round"/>
        ${out ? html`<path d=${line(out, f)} fill="none" stroke="var(--net)" stroke-width="1" stroke-dasharray="3 3"/>` : null}`; });
  }
  // The whole machine that ran it, over the whole axis: CPU in grey, memory dashed.
  const mc = spec.machine || {}, mstep = mc.step || 120, total = mc.total || 25;
  const mrows = (mc.rows || []).filter((m) => m[0] + mstep > t0 && m[0] < t1);
  if (mrows.length) {
    row(mc.title || "MACHINE", "CPU · memory dashed", `${Math.round(avg(mrows.map((m) => m[1])))}% · ${pk(mrows.map((m) => m[2])).toFixed(0)} GB`, `100% · ${Math.round(total)} GB`, (a1, b1) => {
      const f = (v) => b1 - Math.min(v, 100) / 100 * (b1 - a1), g = (v) => b1 - Math.min(v, total) / total * (b1 - a1);
      const grey = "M" + mrows.map((m) => { const p1 = x(m[0]), p2 = x(m[0] + mstep), h = f(m[1]).toFixed(1);
        return `${p1.toFixed(1)},${b1}L${p1.toFixed(1)},${h}L${p2.toFixed(1)},${h}L${p2.toFixed(1)},${b1}`; }).join("L") + "Z";
      // (a new line after a hole of over 10 minutes, as the Mac's band, 28.10.2026)
      const m2 = mrows.map((m, i) => `${i && m[0] - mrows[i - 1][0] <= 600 ? "L" : "M"}${x(m[0] + mstep / 2).toFixed(1)},${g(m[2]).toFixed(1)}`).join("");
      return html`<path d=${grey} fill="var(--machine)"/><path d=${m2} fill="none" stroke="var(--muted)" stroke-dasharray="3 3"/>`; });
  }
  const H = y - 12;
  // The crosshair on a pinned card: the moment under the pointer, what the run and the machine were doing.
  const tAt = (ev) => { const b1 = svgRef.current.getBoundingClientRect(), px = (ev.clientX - b1.left) * W / b1.width;
    return px < X0 || px > X1 ? null : { t: t0 + (px - X0) / (X1 - X0) * (t1 - t0), px, left: ev.clientX - b1.left, top: ev.clientY - b1.top, w: b1.width }; };
  let tip = null;
  if (hv) {
    const t = hv.t, i = u && u.step ? Math.floor((t - u.start) / u.step) : -1;
    const at = (v) => v && i >= 0 && i < v.length ? v[i] : null;
    const seg = segs.find((g) => g[0] <= t && t < g[1]);
    const dlNow = (spec.dl ? spec.dl(u) : []).some(([a1, b1]) => a1 <= t && t < b1 + (u ? u.step : 0));
    const m = mrows.find((r) => r[0] <= t && t < r[0] + mstep);
    const lines = [html`<b>${clock(t, true)}</b>${seg ? ` · ${seg[4] || (seg[3] || [])[0] || ""}` : spec.rest && t >= spec.rest[0] ? " · expected, not yet" : ""}${dlNow ? " · downloading" : ""}`];
    if (at(cpu) != null) lines.push(html`<span class="k">CPU</span> ${at(cpu).toFixed(1)} threads`);
    if (at(mem) != null) lines.push(html`<span class="k">memory</span> ${at(mem).toFixed(2)} GB${plan ? ` of ${plan.toFixed(1)} planned` : ""}`);
    if (at(net) != null) lines.push(html`<span class="k">network</span> ${MBs(at(net))} MB/s in${at(out) != null ? `, ${MBs(at(out))} out` : ""}`);
    if (m) lines.push(html`<span class="k">${(mc.title || "machine").toLowerCase()}</span> CPU ${Math.round(m[1])}% · ${m[2].toFixed(1)} of ${Math.round(total)} GB`);
    if (pub && Math.abs(pub - t) < (t1 - t0) / 60) lines.push(html`<span class="k">GitHub</span> published ${clock(pub)}`);
    tip = { lines, left: hv.left, top: hv.top, flip: hv.left > hv.w * 0.55 };
  }
  const note = !cpu ? (u === undefined ? "the run's samples …" : running ? "the run's samples in a minute" : spec.noSamples || "no samples for this run (recorded from 23.10)") : null;
  const foot = spec.foot ? spec.foot(u, { cpu, mem }) : [];
  return html`<div class="pchart"><svg ref=${svgRef} viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="The run on a timeline: its steps, its CPU, memory and network, and the machine"
      onPointerMove=${pinned ? (ev) => setHv(tAt(ev)) : null} onPointerLeave=${pinned ? () => setHv(null) : null}>
    <defs><pattern id="tl-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="var(--panel)"/><rect width="2.5" height="6" fill="var(--faint)" opacity=".55"/></pattern>
      <pattern id="tl-rest" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="8" height="8" fill="var(--panel)"/><rect width="4" height="8" fill="var(--line)"/></pattern></defs>
    ${ticks.map((t) => html`<line x1=${x(t)} x2=${x(t)} y1="14" y2=${H} stroke="var(--line)"/><text x=${x(t)} y="9" text-anchor="middle">${clock(t)}</text>`)}
    ${shown}${parts}
    ${hv ? html`<line x1=${hv.px} x2=${hv.px} y1=${stripY - 2} y2=${H} stroke="var(--ink)" opacity=".45"/>` : null}
  </svg>${tip ? html`<div class="ttip" style=${`${tip.flip ? `right:${tip.w - tip.left + 12}px` : `left:${tip.left + 12}px`};top:${Math.max(0, tip.top - 10)}px`}>${tip.lines.map((l1, k) => html`${k ? html`<br/>` : null}${l1}`)}</div>` : null}
  ${note ? html`<div class="pnote">${note}</div>` : null}
  ${foot.length ? html`<div class="pfoot">${foot.filter(Boolean).map(([v, k]) => html`<span><b>${v}</b> ${k}</span>`)}</div>` : null}</div>`;
}

// What a bar is (the owner, 22.10.2026: "on hover on each line a popup ... if active, how much it uses, status ...
// combining what we see in Machine; if finished, the same as on click at the bottom"; then "show also available time,
// waited time, download time and when started"): a home run's or WRF's as a timeline (RunTimeline), GitHub's as its steps.
function BarPop({ d, now, left, top, onClose, band, compact, pinned }) {
  const { kind, l } = d;
  const e = d.e || {}, a = e.avail;
  const size = (mb) => mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${Math.round(mb)} MB`;
  const kv = (rows) => html`<div class="pkv">${rows.filter(Boolean).map(([k, v]) => html`<span>${k}</span><span>${v}</span>`)}</div>`;
  // The steps: [time or null, what, detail].
  const steps = (rows) => html`<div class="psteps">${rows.filter(Boolean).map(([t, what, detail]) => html`<span class="st">${t ? clock(t) : ""}</span><span class="sw">${what}</span><span class="sd">${detail || ""}</span>`)}</div>`;
  // A bar of the parts: [seconds, colour], the last one maybe the expected rest (striped).
  const parts = (list, rest) => {
    const total = list.reduce((s, p) => s + Math.max(p[0], 0), 0) + (rest || 0);
    if (total <= 0) return null;
    return html`<div class="pbar">${list.map(([v, c]) => v > 0 ? html`<i style=${`width:${(v / total * 100).toFixed(2)}%;background:${c}`}></i>` : null)}${rest > 0 ? html`<i class="rest" style=${`width:${(rest / total * 100).toFixed(2)}%`}></i>` : null}</div>`;
  };
  const source = (e.availFrom ? `a new input: ${e.availFrom}` : l.wrf ? "GFS at NOAA" : "Open-Meteo's record");
  const gb = (mb) => mb != null ? (mb / 1000).toFixed(1) : "–";
  // The machine rows for a timeline: the Tower's band ([t, CPU of the jobs and WRF %, memory GB]) or the Mac's.
  const towerRows = band ? { rows: (band.rows || []).map((m) => [m[0], m[1] + m[2], m[3]]), step: band.step, total: band.total_gb, title: "TOWER" } : null;
  const macRows = band && band.mac ? { rows: band.mac.rows.map((m) => [m[0], m[1] + m[2], m[3]]), step: band.step, total: band.mac.total_gb, title: "MAC MINI" } : null;
  let body, timeline = false;
  if (kind === "running" || kind === "tower") {
    // A home run: available (or due), not seen or not due yet, waited, running, done; the job's own use; the Tower.
    timeline = true;
    const running = kind === "running";
    const r = running ? d.j : d.t;
    const due = r.due && r.due > 1e9 ? r.due : null;
    const [from, start, end] = homeSpan(d, now);
    const dl = running ? r.dl : r.dl_s != null ? { busy_s: r.dl_s, mb: r.dl_mb, files: r.dl_files } : null;
    const ran = Math.max(0, end - start);
    const je = running ? jobEnd(r, now) : null;
    const okTag = running ? (je && je.late ? html`<span class="ptag late">running long</span>` : html`<span class="ptag">running · ${r.live ? "live" : "shadow"}</span>`)
      : html`<span class=${"ptag " + (r.exit === 0 || r.ok ? "done" : "bad")}>${(r.exit === 0 || r.ok ? "finished · " : "failed · ") + (r.host === "mac" ? "Mac mini" : "Tower")}</span>`;
    const fromWhat = a != null && from === a ? "available" : r.avail && from === r.avail ? "available" : "due";
    // Before it was due: a job that watches a model hadn't seen its run; one made from our own sets (a new input) or on
    // a timer wasn't due yet (its inputs, its gap after its last run, its hour).
    const ownInputs = !!(e.availFrom || r.availFrom);
    const seenAt = due && due > from + 30 ? Math.min(due, start) : from;
    const spec = { t0: from, tEnd: end, gh: { pub: e.pub, start: e.gh && e.gh.start },
      rest: running && je && !je.late ? [now, je.end] : null, machine: towerRows,
      marks: [[from, `${fromWhat} ${clock(from)}`, "var(--accent)"], start - from >= 30 ? [start, `started ${clock(start)}`, "var(--home)"] : null,
        running ? [now, `now ${clock(now)}`, "var(--warn)"] : [end, `${r.exit === 0 || r.ok ? "done" : "failed"} ${clock(end)}`, r.exit === 0 || r.ok ? "var(--ink)" : "var(--bad)"],
        running && je && !je.late ? [je.end, `expected ${clock(je.end)}`, "var(--muted)"] : null],
      segs: (u) => {
        const wait = u && u.wait ? Object.entries(u.wait).sort((p, q) => q[1] - p[1]) : [];
        const w0 = wait[0], wm = mins(start - seenAt), nm = mins(seenAt - from);
        const label = ownInputs ? "not due yet" : "not seen yet";
        return [[from, seenAt, "url(#tl-hatch)", [`${label} · ${nm}`, nm],
            ownInputs ? "not due yet: it waits for its inputs, its gap after its last run, or its hour" : "not seen yet: the run was out, the scheduler hadn't seen it"],
          [seenAt, start, "var(--wait)", [w0 ? `waited ${wm} · ${WHY[w0[0]] || w0[0]}` : null, w0 ? `waited ${wm} · ${WHY_SHORT[w0[0]] || w0[0]}` : null, `waited ${wm}`, wm],
            `waited: the scheduler held it${wait.length ? ` (${wait.map(([k, n]) => `${WHY[k] || k} ${n} min`).join(", ")})` : ""}`],
          [start, end, "var(--home)", [`${running ? "running" : "ran"} ${mins(ran)}`, mins(ran)], running ? "running" : "running"]];
      },
      dl: (u) => u && u.net && u.net.length ? u.net.map((v, i) => v > 1 ? [u.start + i * u.step, u.start + (i + 1) * u.step] : null).filter(Boolean)
        : dl && dl.busy_s > 5 ? [[Math.max(dl.first || start, start), Math.min(Math.max(dl.first || start, start) + dl.busy_s, end)]] : [],
      foot: () => running
        ? [[mins(ran), je ? `running, its usual ${Math.round(je.typ / 60)} min` : "running"], [r.cpu != null ? `${r.cpu}%` : "–", "CPU now"],
          [`${r.mem != null ? GB(r.mem) : "–"}${r.need ? ` of ${(r.need / 1000).toFixed(1)}` : ""} GB`, "memory now"], dl ? [size(dl.mb), `down · ${dl.files} files`] : null]
        : [[mins(end - from), `from ${fromWhat}`], e.pub ? [mins(e.pub - from), "GitHub"] : null, r.cpu != null ? [Math.round(r.cpu), "CPU min"] : null,
          r.peak ? [`${gb(r.peak)} GB`, "peak"] : null, r.net != null ? [`${gb(r.net)} GB`, `down${dl && dl.files ? ` · ${dl.files} files` : ""}`] : null] };
    body = html`<div class="ph"><b>${l.label}${d.e && e.run ? ` · ${runZ(e.run)}` : ""}</b>${okTag}</div>
      <div class="psub">${r.job}${r.priority ? ` · priority ${r.priority}` : ""} · ${r.live ? "live (published)" : "shadow (not published)"}${fromWhat !== "available" ? "" : ownInputs ? ` · a new input: ${e.availFrom || r.availFrom}` : l.wrf ? " · GFS at NOAA" : " · available per Open-Meteo's record"}</div>
      <${RunTimeline} q=${`job=${encodeURIComponent(r.job)}&start=${Math.round(start)}${due ? `&due=${Math.round(due)}` : ""}`} id=${`${r.job}|${Math.round(start)}`}
        running=${running} now=${now} spec=${spec} compact=${compact} pinned=${pinned} />
      ${running ? html`<div class="pspark"><span>↓ ${r.download ? r.download.host : "no download now"}</span><${Spark} values=${r.speeds} w=${150} h=${22} /><b>${MBs(r.net_in || 0)} MB/s</b></div>
        ${r.download ? html`<div class="plog">${base(r.download.url)}${r.download.done ? " · done" : ""}</div>` : null}` : null}`;
  } else if (kind === "wrf" || kind === "wrfdone" || kind === "wrfwait") {
    // WRF, on the Mac mini or the Tower (the owner, 25.10.2026: "card same as for other tasks same for tasks on mac
    // mini"): waiting for GFS at NOAA, its download, WPS, the model computing, the tiles; its own CPU, memory and
    // network (the Mac's beats; the Tower's minute samples, no network of its own) over the machine that ran it.
    timeline = true;
    const running = kind !== "wrfdone", waiting = kind === "wrfwait";
    const w = running ? (d.w || {}) : {};
    const t = d.t || e.tower || {};
    const host = t.host || "tower", onMac = host === "mac";
    const at = w.at || {};
    const ph = (running ? (w.phases || { gfs_done: at.wps, wps: at.wps, model: at.wrf, end: at.tiles }) : t.phases) || {};
    const start = (running ? w.started : t.start) || t.start;
    const out = ph.gfs_out, ready = ph.gfs_done || ph.wps, model = ph.model || (running ? w.computing_from : null), tiles = ph.end;
    const end = running ? now : t.end;
    const ok = running || t.ok;
    const x1 = running && !waiting ? wrfEnd(w, now) : null;
    const machineName = onMac ? "Mac mini" : "Tower";
    const comp = onMac ? "var(--mac)" : "var(--wrf)";
    const firstStep = out || ready || model || tiles || end;
    const spec = { t0: start, tEnd: end, rest: x1 && x1.end > now ? [now, x1.end] : null, machine: onMac ? macRows : towerRows, stripLabel: "WRF",
      noSamples: onMac ? "no samples of the Mac for this run" : "no samples for this run",
      marks: [[start, `started ${clock(start)}`, "var(--accent)"], model ? [model, `computing ${clock(model)}`, comp] : null,
        running ? [now, `now ${clock(now)}`, "var(--warn)"] : [end, `${ok ? "done" : "failed"} ${clock(end)}`, ok ? "var(--ink)" : "var(--bad)"],
        x1 && x1.end > now ? [x1.end, `expected ${clock(x1.end)}`, "var(--muted)"] : null],
      segs: () => [[start, waiting ? now : firstStep, "url(#tl-hatch)", [`waiting for GFS · ${mins((waiting ? now : firstStep) - start)}`, `GFS · ${mins((waiting ? now : firstStep) - start)}`, mins((waiting ? now : firstStep) - start)],
          "waiting for GFS at NOAA: WRF starts at its time and waits there for the run"],
        out && ready ? [out, ready, "var(--accent)", ["GFS"], `downloading GFS${ph.gfs_mb ? ` (${ph.gfs_mb} MB)` : ""}`] : null,
        ready && model ? [ready, model, "var(--gh)", ["WPS"], "WPS: the model's input from GFS"] : null,
        model ? [model, tiles || end, comp, [`computing ${mins((tiles || end) - model)}`, mins((tiles || end) - model)], "the model computing"] : null,
        tiles ? [tiles, end, "var(--home)", ["tiles"], "the tiles made and published"] : null],
      foot: (u, s1) => {
        const cpuMin = u && u.cpu ? Math.round(u.cpu.reduce((s2, v) => s2 + (v || 0), 0) / 100 * u.step / 60) : null;
        return running
          ? [waiting ? [mins(now - start), "waiting for GFS"] : [mins(now - start), "running"], x1 ? [`${Math.round(x1.pct * 100)}%`, `model time ${w.model_time || "–"}`] : null,
            w.cpu != null ? [`${w.cpu}%`, "CPU now"] : null, w.mem != null ? [`${GB(w.mem)} GB`, "memory now"] : null]
          : [[mins(end - start), "from its start"], model ? [mins((tiles || end) - model), "computing"] : null, e.pub ? [clock(e.pub), `published${a ? ` · ${mins(e.pub - a)} from GFS out` : ""}`] : null,
            cpuMin ? [cpuMin, "CPU min"] : null, s1.mem ? [`${Math.max(...s1.mem.filter((v) => v != null)).toFixed(1)} GB`, "peak"] : null];
      } };
    body = html`<div class="ph"><b>${l.label}${e.run ? ` · ${runZ(e.run)}` : ""}</b><span class=${"ptag " + (running ? "wrf" : ok ? "done" : "bad")}>${waiting ? "waiting for GFS" : running ? "running" : ok ? "finished" : "failed"} · ${machineName}</span></div>
      <div class="psub">GFS ${runZ(e.run)} run · our own model, 1 km${onMac ? " · on the Mac mini" : ""}</div>
      <${RunTimeline} q=${`job=wrf&host=${host}&start=${Math.round(start)}${running ? "" : `&end=${Math.round(end)}`}`} id=${`wrf|${host}|${Math.round(start)}`}
        running=${running} now=${now} spec=${spec} compact=${compact} pinned=${pinned} />
      ${running && w.last_line ? html`<div class="plog">${w.last_line}</div>` : null}`;
  } else {
    const gs = e.gh ? e.gh.start : null, waited = gs != null && a != null ? Math.max(0, gs - a) : 0, run = e.pub && gs != null ? e.pub - Math.max(gs, a ?? gs) : 0;
    body = html`<div class="ph"><b>${l.label} · ${runZ(e.run)}</b><span class="ptag gh">GitHub</span></div>
      <div class="psub">${l.wf || "workflow"} · ${e.pub ? "published" : e.failed ? "failed" : "running"}</div>
      ${parts([[waited, "var(--wait)"], [run, "var(--gh)"]], 0)}
      ${steps([a != null ? [a, "available", source] : null,
        gs != null ? [gs, "job started", a != null ? `waited ${mins(waited)} (its schedule)` : ""] : [null, "job", "its start not known"],
        e.pub ? [e.pub, "published", a != null ? `${mins(e.pub - a)} from available` : ""] : null])}
      ${kv([["Use", "GitHub doesn't report it per run"]])}`;
  }
  return html`<div class=${"bpop" + (timeline && !compact ? " wide" : "") + (pinned ? " pinned" : "")} style=${`left:${left}px;top:${top}px`}>
    <button class="px" aria-label="Close" onClick=${(ev) => { ev.stopPropagation(); onClose && onClose(); }}>✕</button>${body}</div>`;
}

const LANES_MIN_W = 760;
function Lanes({ lanes, now, sel, setSel, back, ahead, band, slots, wrf, head }) {
  const t0 = now - back * 3600, t1 = now + ahead * 3600, lh = 30, gh = 24, PITCH = 9;
  const box = useRef(null);
  // Drawn at the box's own width, 1:1 (the owner, 22.10.2026: zoomed in, the lanes scrolled sideways; "I want to have
  // zoom in/out"): the names and the right columns keep their size, the hours take the rest, so a zoom makes the text
  // bigger and the hours narrower. Under LANES_MIN_W (a phone, the owner 22.10.2026: "on my phone not looking good")
  // each lane's name and numbers go on a line above its bars, and the bars take the whole width.
  const [boxW, setBoxW] = useState(0);
  useEffect(() => {
    const measure = () => { if (box.current) setBoxW(Math.floor(box.current.clientWidth)); };
    measure();
    // A browser zoom is a window resize; the observer also catches the box changing on its own.
    addEventListener("resize", measure);
    const ro = typeof ResizeObserver !== "undefined" && box.current ? new ResizeObserver(measure) : null;
    if (ro) ro.observe(box.current);
    return () => { removeEventListener("resize", measure); if (ro) ro.disconnect(); };
  }, []);
  const compact = boxW > 0 && boxW < LANES_MIN_W, NAME_H = compact ? 16 : 0;
  const W = compact ? boxW : Math.max(LANES_MIN_W, boxW || 1460), X0 = compact ? 6 : 244, X1 = compact ? W - 6 : W - 196;
  const x = (t) => X0 + (Math.max(t0, Math.min(t1, t)) - t0) / (t1 - t0) * (X1 - X0);
  const w = (a, b, min = 1.5) => Math.max(x(b) - x(a), min);
  // The hours' labels at least ~70 px apart (at the usual width: each hour on 6 h, every 2 on 12 h, every 3 on 24 h).
  const step = [1, 2, 3, 6].find((k) => (X1 - X0) / ((t1 - t0) / 3600) * k >= 70) || 6;
  const svgRef = useRef(null);
  // The machine band (and the hours) in a drawing of its own, pinned under the page's header while the lanes scroll
  // (the owner, 22.10.2026: "as the phone app's forecast keeps its days ... leave always on top the part with usage",
  // with a switch for it; remembered in this browser, on at first). The same coordinates as the lanes' drawing.
  const wrapRef = useRef(null), bandRef = useRef(null), bandSvg = useRef(null);
  const [pinned, setPinnedState] = useState(() => { try { return localStorage.getItem("ops-band-pinned") !== "0"; } catch (e) { return true; } });
  const setPinned = (v) => { try { localStorage.setItem("ops-band-pinned", v ? "1" : "0"); } catch (e) {} setPinnedState(v); };
  // On a phone the page's header scrolls away and only the processes stay (the owner, 22.10.2026: "on phone scroll
  // away but we leave processes only"): the band sticks at the screen's top with its title and filters above it
  // scrolled out (a negative top, their height). Measured after each render (the filters' lines change with the width).
  const [phoneTop, setPhoneTop] = useState(null);
  const measureTop = () => {
    const top = document.querySelector(".top"), phone = top && getComputedStyle(top).position !== "sticky";
    document.documentElement.style.setProperty("--top-h", (top && !phone ? top.offsetHeight : 0) + "px");
    const off = phone && bandRef.current ? -(bandRef.current.offsetTop - 4) : null;
    if (off !== phoneTop) setPhoneTop(off);
  };
  useEffect(measureTop);
  useEffect(() => { addEventListener("resize", measureTop); return () => removeEventListener("resize", measureTop); });
  const [tip, setTip] = useState(null);
  const [pop, setPop] = useState(null);
  // A bar's popup on hover or tap (data-bar: the band's handler leaves it).
  const showPop = (ev, d, pinned) => {
    if (!box.current) return;
    const b = box.current.getBoundingClientRect();
    // Above the pointer near the chart's foot (below it hung past the edge: an inner scrollbar, the popup hidden).
    const y = ev.clientY - b.top;
    // A home run's or WRF's popup has its timeline (23.10.2026): taller, and wider off a phone.
    const home = ["running", "tower", "wrf", "wrfdone", "wrfwait"].includes(d.kind), ph = home ? 520 : 290, pw = home && !compact ? 570 : 390;
    setPop({ d, pinned: !!pinned, left: Math.max(box.current.scrollLeft + 4, Math.min(ev.clientX - b.left + box.current.scrollLeft + 14, box.current.scrollLeft + b.width - pw)),
      top: y + ph > b.height ? Math.max(4, y - ph) : y + 14 });
  };
  // A mouse (or pen) shows a bar's popup on hover; a click or a tap pins it where it is (the owner, 25.10.2026: "on
  // click card stay on place (now on click we go on bottom for additional info - dont need it)"; on a phone a tap did
  // already, 22.10.2026). While one is pinned, hovering other bars leaves it; a click on another bar pins that one.
  // A finger's pointerleave comes as it lifts, so a tap's popup isn't closed by it.
  const touched = useRef(false);  // the band's tip: a tap or a mouse
  const hov = (d) => ({ "data-bar": 1, onPointerEnter: (ev) => { if (ev.pointerType !== "touch" && !(pop && pop.pinned)) showPop(ev, d); },
    onPointerLeave: (ev) => { if (ev.pointerType !== "touch" && !(pop && pop.pinned)) setPop(null); },
    onClick: (ev) => { ev.stopPropagation(); showPop(ev, d, true); } });
  // A click off the bars and off the popup closes it, so does Esc or its ✕; off the band and its tip, the tip. On a
  // click, not a touch's start: a scroll (on the popup, to read its foot, or anywhere) leaves them open (the owner,
  // 22.10.2026: "if I scroll on popup window it's not closes. If I hit out of window it's closes"; 25.10.2026: "close
  // button. Or, if we click on outside of card we close card").
  useEffect(() => {
    const off = (ev) => { const t = ev.target && ev.target.closest ? ev.target : null;
      if (!(t && (t.closest("[data-bar]") || t.closest(".bpop")))) setPop(null);
      if (!(t && (t.closest(".bandbox") || t.closest(".btip")))) setTip(null); };
    const esc = (ev) => { if (ev.key === "Escape") { setPop(null); setTip(null); } };
    document.addEventListener("click", off);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("click", off); document.removeEventListener("keydown", esc); };
  }, []);
  // A running bar's rest, dashed to its expected end, labelled.
  const expect = (y, end, color, label, late) => html`<line x1=${x(now)} x2=${x(Math.max(end, now))} y1=${y + 20} y2=${y + 20} stroke=${late ? "var(--wait)" : color} stroke-width="3" stroke-dasharray="5 4"/>
    <rect x=${x(Math.max(end, now)) - 1} y=${y + 14} width="2" height="12" fill=${late ? "var(--wait)" : color} opacity=".8"/>
    <text class="eta" x=${Math.min(x(Math.max(end, now)) + 6, W - 4 - label.length * 6.8)} y=${y + 24} style=${`fill:${late ? "var(--warn)" : color}`}>${label}</text>`;
  const wEnd = wrfEnd(wrf, now);
  useEffect(() => {
    if (!box.current) return;
    box.current.scrollLeft = box.current.scrollWidth;
    if (bandRef.current) bandRef.current.scrollLeft = box.current.scrollLeft;
  }, [back]);
  const BH = band && band.rows && band.rows.length ? BAND_H + (band.mac && band.mac.rows.length ? MAC_H : 0) : 0;
  // Runs that overlap in a lane get sub-rows (the owner, 22.10.2026: "instead of overlapping, we have multiple rows
  // showing one after another"; mockup pipeline-overlap-2026-10-22): each source's bars (GitHub's, the Tower's) packed
  // into the fewest rows, a run into the first row free by its start; a lane grows 9 px a row while an overlap is in view.
  const sec = (t1 - t0) / (X1 - X0), labelEnd = (end, label) => Math.max(end, now) + (label.length * 6.8 + 10) * sec;
  const runningLabel = (je) => je && (je.late ? `${Math.round((now - je.start) / 60)} min · over its usual ${Math.round(je.typ / 60)}` : `${Math.round(je.pct * 100)}% · ~${clock(je.end)}`);
  const layout = (l) => {
    const gb = [], tb = [];
    for (const e of l.entries) {
      const a = e.avail;
      const end = Math.max(e.pub || 0, e.tower ? e.tower.end : 0, e.gh ? e.gh.end || now : 0, a || 0);
      if (end < t0 || (a || e.pub || e.run) > t1 || e.skipped) continue;
      if (!l.wrf && (!l.homeOnly || e.gh)) {
        const gs = e.gh ? e.gh.start : null, ge = e.pub || (e.gh && e.gh.end == null ? now : null);
        const to = ge || (a != null && now - a < 3 * 3600 ? now : null);
        if (to) gb.push({ key: e, from: a != null ? a : gs != null ? gs : to, to });
      }
      const t = e.tower;
      if (t) tb.push({ key: e, from: a != null ? Math.min(a, t.start) : t.start,
        to: t.going && wEnd ? labelEnd(wEnd.end, `${Math.round(wEnd.pct * 100)}% · ~${clock(wEnd.end)}`) : t.end });
    }
    for (const r of l.loose) if (r.end >= t0 && r.start <= t1) tb.push({ key: r, from: r.avail || (r.due && r.start - r.due > 60 ? r.due : r.start), to: r.end });
    if (l.home && l.home.started) { const je = jobEnd(l.home, now);
      tb.push({ key: "home", from: l.homeAvail ? Math.min(l.homeAvail.avail, l.home.started) : l.home.started, to: je ? labelEnd(je.late ? now : je.end, runningLabel({ ...je, start: l.home.started })) : now }); }
    const pack = (list) => {
      const ends = [], at = new Map();
      list.sort((p, q) => Math.max(p.from, t0) - Math.max(q.from, t0) || p.to - q.to);
      for (const b of list) {
        let k = ends.findIndex((e) => e + 3 * sec <= Math.max(b.from, t0));
        if (k < 0) { k = ends.length; ends.push(0); }
        ends[k] = b.to; at.set(b.key, k);
      }
      return [Math.max(1, ends.length), at];
    };
    const [kg0, gAt] = pack(gb), [kt, tAt] = pack(tb), kg = gb.length || !l.homeOnly ? kg0 : 0;
    return { kg, kt, g: (key) => PITCH * (gAt.get(key) || 0), t: (key) => PITCH * (kg - 1 + (tAt.get(key) || 0)), h: lh + PITCH * (kg + kt - 2),
      hasG: (key) => gAt.has(key), hasT: (key) => tAt.has(key) };
  };
  // Each run's "available" joined to its bars in a lane with sub-rows (the owner, 22.10.2026, red lines on a
  // screenshot: "where indicated every task when it was available and to what it's related"), drawn over the bars it
  // passes; the Tower's own triangles where its "available" is its own (a mix's home runs, the job running now).
  const tri = (a, y, tip) => html`<path d=${`M${x(a) - 4} ${y + 1} L${x(a) + 4} ${y + 1} L${x(a)} ${y + 6} Z`} fill="var(--muted)"><title>${tip}</title></path>`;
  const links = (l, y, lay) => {
    const out = [], grown = lay.kg + lay.kt > 2;
    const link = (a, top) => { if (grown && a >= t0 && a <= t1 && top > y + 10) out.push(html`<line x1=${x(a)} x2=${x(a)} y1=${y + 6} y2=${top + 3} stroke="var(--ink)" stroke-width="1" opacity=".55" pointer-events="none"/>`); };
    for (const e of l.entries) if (e.avail != null && !e.skipped) {
      if (lay.hasG(e)) link(e.avail, y + lay.g(e) + 8);
      if (lay.hasT(e)) link(e.avail, y + lay.t(e) + 17);
    }
    for (const r of l.loose) if (r.avail && r.avail >= t0 && lay.hasT(r)) {
      out.push(tri(r.avail, y, `${l.label} · at home: available ${clock(r.avail)} (a new input: ${r.availFrom} published)`));
      link(r.avail, y + lay.t(r) + 17);
    }
    const h = l.homeAvail;
    if (h && h.avail >= t0 && lay.hasT("home")) {
      out.push(tri(h.avail, y, `${l.label} · running at home: available ${clock(h.avail)}${h.availFrom ? ` (a new input: ${h.availFrom} published)` : h.run ? ` (its ${runZ(h.run)} run)` : ""}`));
      link(h.avail, y + lay.t("home") + 17);
    }
    return out;
  };
  const rows = [];
  let y = 30 + BH;
  for (const g of GROUPS) {
    const ls = lanes.filter((l) => l.group === g);
    if (!ls.length) continue;
    rows.push({ g, y }); y += gh;
    for (const l of ls) { const lay = layout(l); rows.push({ l, y, lay }); y += lay.h + NAME_H; }
  }
  const H = y + 22;
  const hours = [];
  for (let t = Math.ceil(t0 / 3600) * 3600; t <= t1; t += 3600) hours.push(t);
  const runTip = (l, e) => `${l.label} ${runZ(e.run)}${e.avail != null ? ` · available ${clock(e.avail)}` : ""}`;
  const entry = (l, e, y, lay) => {
    // A live feed's publish: a mark on its bars' line (thinner for the frequent ones).
    if (l.feed) {
      if (!e.pub || e.pub < t0 || e.pub > t1) return [];
      const tw = l.feed.every < 1800 ? 1.5 : 2.5;
      return [html`<rect x=${x(e.pub) - tw / 2} y=${y + lay.t(null) + 15} width=${tw} height="10" fill="var(--ok)"><title>${l.label}: published ${clock(e.pub)}</title></rect>`];
    }
    const a = e.avail, out = [], yg = y + lay.g(e), yt = y + lay.t(e);
    const end = Math.max(e.pub || 0, e.tower ? e.tower.end : 0, e.gh ? e.gh.end || now : 0, a || 0);
    if (end < t0 || (a || e.pub || e.run) > t1) return out;
    if (a != null) out.push(html`<path d=${`M${x(a) - 4} ${y + 1} L${x(a) + 4} ${y + 1} L${x(a)} ${y + 6} Z`} fill="var(--muted)"><title>${runTip(l, e)} (${e.availFrom ? `a new input: ${e.availFrom} published` : l.wrf ? (e.early ? "GFS was already out at NOAA when WRF started" : "GFS out at NOAA: WRF had waited for it") : "Open-Meteo's record"})</title></path>`);
    if (e.skipped) {
      out.push(html`<text x=${x(a) + 3} y=${y + 20} class="skip">×<title>${runTip(l, e)}: not taken (no publish on GitHub or at home)</title></text>`);
      return out;
    }
    // GitHub, the upper bar: waiting (amber) from available to its job's start, then the job (grey) to the publish; in a
    // lane made at home only, just where GitHub stood in.
    if (!l.wrf && (!l.homeOnly || e.gh)) {
      const gs = e.gh ? e.gh.start : null, ge = e.pub || (e.gh && e.gh.end == null ? now : null);
      const from = a != null ? a : gs;
      if (from != null && ge) {
        const procFrom = gs != null && a != null ? Math.max(gs, a) : from;
        out.push(html`<g ...${hov({ kind: "gh", l, e })}>${a != null && procFrom > a ? html`<rect x=${x(a)} y=${yg + 8} width=${w(a, procFrom, 1)} height="6" rx="1" fill="var(--wait)"/>` : null}
          <rect x=${x(procFrom)} y=${yg + 8} width=${w(procFrom, ge)} height="6" rx="1" fill=${e.failed ? "var(--bad)" : "var(--gh)"} opacity=${gs == null && a != null ? 0.35 : 1}/>
          ${e.pub ? html`<rect x=${x(e.pub) - 1} y=${yg + 6} width="2.5" height="10" fill="var(--ok)"/>` : null}</g>`);
      } else if (e.pub) out.push(html`<g ...${hov({ kind: "gh", l, e })}><rect x=${x(e.pub) - 1} y=${yg + 6} width="2.5" height="10" fill="var(--ok)"/></g>`);
      else if (a != null && now - a < 3 * 3600) out.push(html`<g ...${hov({ kind: "gh", l, e })}><rect x=${x(a)} y=${yg + 8} width=${w(a, now, 1)} height="6" rx="1" fill="var(--wait)"/></g>`);
    }
    // The Tower, the lower bar: waiting, then its run (faint: shadow, not published).
    const t = e.tower;
    // WRF started by its task, still waiting for NOAA's GFS (22.10.2026: it showed nothing): a waiting bar to now.
    if (t && t.waiting) {
      out.push(html`<g ...${hov({ kind: "wrfwait", l, e, w: wrf, t })}><rect x=${x(t.start)} y=${yt + 17} width=${w(t.start, t.end, 1)} height="6" rx="1" fill="var(--wait)"/></g>`);
    } else if (t) {
      const procFrom = a != null ? Math.max(a, t.start) : t.start;
      out.push(html`<g ...${hov(t.going ? { kind: "wrf", l, e, w: wrf } : l.wrf ? { kind: "wrfdone", l, e, t } : { kind: "tower", l, e, t })}>
        ${a != null && t.start > a ? html`<rect x=${x(a)} y=${yt + 17} width=${w(a, t.start, 1)} height="6" rx="1" fill="var(--wait)"/>` : null}
        <rect x=${x(procFrom)} y=${yt + 17} width=${w(procFrom, t.end)} height="6" rx="1" fill=${!t.ok ? "var(--bad)" : t.host === "mac" ? "var(--mac)" : "var(--home)"} opacity=${t.live ? 1 : 0.55}/>
        ${((p) => p ? html`<rect x=${x(p[0])} y=${yt + 17} width=${w(p[0], p[1], 1)} height="6" rx="1" fill="var(--accent)" opacity=${t.live ? 1 : 0.7}/>` : null)(l.wrf ? null : dlPart(t, procFrom, t.end))}
        ${t.live && t.ok && !t.going ? html`<rect x=${x(l.wrf && e.pub ? e.pub : t.end) - 1} y=${yt + 15} width="2.5" height="10" fill="var(--ok)"/>` : null}
        ${t.going && wEnd ? expect(yt, wEnd.end, t.host === "mac" ? "var(--mac)" : "var(--wrf)", `${Math.round(wEnd.pct * 100)}% · ~${clock(wEnd.end)}`, false) : null}</g>`);
    }
    return out;
  };
  // The band's minute under the pointer (hover, or a tap on a phone).
  const point = (ev) => {
    if (!BH || !bandSvg.current || !wrapRef.current) return setTip(null);
    const pt = bandSvg.current.createSVGPoint();
    pt.x = ev.clientX; pt.y = ev.clientY;
    const p = pt.matrixTransform(bandSvg.current.getScreenCTM().inverse());
    if (p.x < X0 || p.x > X1 || p.y > 30 + BH - 10) return setTip(null);
    const t = t0 + (p.x - X0) / (X1 - X0) * (t1 - t0);
    const near = band.rows.reduce((a, r) => Math.abs(r[0] - t) < Math.abs(a[0] - t) ? r : a, band.rows[0]);
    if (Math.abs(near[0] - t) > 600) return setTip(null);
    const b = wrapRef.current.getBoundingClientRect();
    setTip({ row: near, left: Math.max(4, Math.min(ev.clientX - b.left + 14, b.width - 310)), top: ev.clientY - b.top + 12 });
  };
  // A tap off the bars closes a bar's popup.
  const offBar = (ev) => { if (ev.pointerType !== "touch" && !(ev.target && ev.target.closest && ev.target.closest("[data-bar]"))) setPop(null); };
  // Where the lanes begin: above it the hours and the band (the pinned drawing), below the lanes (their drawing, the same
  // coordinates shown from TOP down).
  const TOP = 30 + BH - 10;
  // The section's title and filters pinned with it (the owner, 22.10.2026: "lets pin this too"), the switch beside them.
  return html`<div class=${"bandwrap" + (pinned ? " pinned" : "")} ref=${wrapRef} style=${pinned && phoneTop != null ? `top:${phoneTop}px` : ""}>
    <div class="bandhead"><div class="bandtitle">${head}</div>
    <button class=${"pin" + (pinned ? " on" : "")} onClick=${() => setPinned(!pinned)} aria-pressed=${pinned}
      title=${pinned ? "The title, filters and machine band stay on top while you scroll: tap to let them scroll away" : "Keep the title, filters and machine band on top while you scroll"}>${pinned ? "● Pinned" : "○ Pin"}</button></div>
    <div class="lanes bandbox" ref=${bandRef}><svg ref=${bandSvg} viewBox="0 0 ${W} ${TOP}" style=${`min-width:${W}px`} role="img" aria-label="The machine: CPU, jobs, memory, network"
      onPointerMove=${(ev) => { if (ev.pointerType !== "touch") point(ev); }} onPointerDown=${(ev) => { touched.current = ev.pointerType === "touch"; }}
      onClick=${(ev) => { if (touched.current) point(ev); }} onPointerLeave=${(ev) => { if (ev.pointerType !== "touch") setTip(null); }}>
    ${hours.map((t) => html`<line x1=${x(t)} x2=${x(t)} y1="22" y2=${TOP} stroke="var(--line)"/>${Math.round(t / 3600) % step === 0 && x(t) > 18 && x(t) < W - 18 ? html`<text x=${x(t)} y="15" text-anchor="middle">${clock(t)}</text>` : null}`)}
    ${BH ? bandLayer(band, x, t0, slots || 4, X0, X1, compact) : null}
    ${BH && band.mac && band.mac.rows.length ? macBandLayer(band.mac, x, t0, X0, X1, compact, BAND_H + 14) : null}
    ${(() => {
      // The open card's run on the band (the owner, 23.10.2026: "plus the highlight on the band"): the card's timeline,
      // from available (lighter: before it started) to its end (a running one: now), so the card and the machine line up.
      const sp = pop && (pop.d.kind === "tower" || pop.d.kind === "running") ? homeSpan(pop.d, now) : null;
      if (!BH || !sp || sp[2] < t0) return null;
      const [f0, s0, e0] = sp, xa = Math.max(x(f0), X0), xs = Math.max(x(s0), X0), xb = Math.max(Math.min(x(e0), X1), xs + 2);
      return html`${xs > xa + 1 ? html`<rect x=${xa} y="20" width=${xs - xa} height=${TOP - 20} fill="var(--hl)" opacity=".5"/>` : null}
        <rect x=${xs} y="20" width=${xb - xs} height=${TOP - 20} fill="var(--hl)"/>
        <line x1=${xa} x2=${xa} y1="20" y2=${TOP} stroke="var(--ink)" opacity=".35" stroke-dasharray="2 3"/>
        <line x1=${xs} x2=${xs} y1="20" y2=${TOP} stroke="var(--ink)" opacity=".5"/><line x1=${xb} x2=${xb} y1="20" y2=${TOP} stroke="var(--ink)" opacity=".5"/>`;
    })()}
    ${compact ? null : html`<text class="side" x=${X1 + 10} y="15">Tower</text><text class="side" x=${X1 + 54} y="15" style="fill:var(--mac)">Mac</text><text class="side" x=${X1 + 98} y="15">GitHub</text><text class="side" x=${X1 + 142} y="15">skipped</text>`}
    <line x1=${x(now)} x2=${x(now)} y1="20" y2=${TOP} stroke="var(--warn)" stroke-width="2"/>
    ${tip ? html`<line x1=${x(tip.row[0] + 60)} x2=${x(tip.row[0] + 60)} y1="20" y2=${30 + BH - 10} stroke="var(--ink)" opacity=".45"/>` : null}
    </svg></div>${tip ? html`<${BandTip} band=${band} row=${tip.row} left=${tip.left} top=${tip.top} />` : null}</div>
  <div class="lanes" ref=${box} onScroll=${() => { if (bandRef.current && box.current) bandRef.current.scrollLeft = box.current.scrollLeft; }}><svg ref=${svgRef} viewBox="0 ${TOP} ${W} ${H - TOP}" style=${`min-width:${W}px`} role="img" aria-label="Every map's runs from available to published"
      onPointerDown=${offBar}>
    ${hours.map((t) => html`<line x1=${x(t)} x2=${x(t)} y1=${TOP} y2=${H - 20} stroke="var(--line)"/>`)}
    ${rows.map(({ g, l, y: y0, lay }) => { if (g) return html`<text class="grp" x="0" y=${y0 + 16}>${g.toUpperCase()}</text>
      ${compact ? html`<text class="grp" x=${W} y=${y0 + 16} text-anchor="end"><tspan style="fill:var(--home)">TOWER</tspan> · <tspan style="fill:var(--mac)">MAC</tspan> · GITHUB · <tspan style="fill:var(--bad)">SKIPPED</tspan></text>` : null}`;
      // On a phone the lane's name line, then its bars from y (below it).
      const y = y0 + NAME_H, dot = l.status === "bad" ? "var(--bad)" : l.status === "proc" ? "var(--accent)" : l.status === "wait" ? "var(--warn)" : "var(--ok)";
      // The lane's details below open from its name only (the left column; a phone: its name line), not its bars or
      // the empty time (the owner, 25.10.2026: a click pins a bar's card instead of jumping to them).
      const onName = (ev) => { const b = ev.currentTarget.ownerSVGElement.getBoundingClientRect();
        return compact ? TOP + (ev.clientY - b.top) * ((H - TOP) / b.height) < y0 + NAME_H + 2 : (ev.clientX - b.left) * (W / b.width) < X0; };
      return html`<g class=${"row" + (sel === l.name ? " sel" : "")} onClick=${(ev) => { if (onName(ev)) setSel(l.name); }}>
      <rect class="hit" x="0" y=${y0} width=${W} height=${lay.h + NAME_H} fill="transparent" rx="4"/>
      ${compact ? html`<circle cx="5" cy=${y0 + 9} r="4" fill=${dot}/>
      <text class="lname cap" x="14" y=${y0 + 13}>${((n) => l.label.length > n ? l.label.slice(0, n - 1) + "…" : l.label)(
        Math.max(8, Math.floor((W - 26 - (mins(l.towerT) + " · " + mins(l.ghT) + (l.skipped ? ` · ${l.skipped}/${l.offered}` : "")).length * 7.3) / 6.6)))}</text>
      <text class="ours cap" x=${W - 2} y=${y0 + 13} text-anchor="end"><tspan style=${"fill:var(--home)" + (l.procOnly ? ";font-style:italic" : "")}>${mins(l.towerT)}</tspan><tspan style="fill:var(--faint)"> · </tspan>${l.macT != null ? html`<tspan style="fill:var(--mac)">${mins(l.macT)}</tspan><tspan style="fill:var(--faint)"> · </tspan>` : null}<tspan style=${"fill:var(--muted)" + (l.procOnly ? ";font-style:italic" : "")}>${mins(l.ghT)}</tspan>${l.skipped ? html`<tspan style="fill:var(--bad)"> · ${l.skipped}/${l.offered}</tspan>` : null}</text>`
      : html`${l.wrf || !lay.kg ? null : html`<text class="src" x=${X0 - 6} y=${y + 14} text-anchor="end">GitHub${lay.kg > 1 ? " ×" + lay.kg : ""}</text>`}
      <text class="src" x=${X0 - 6} y=${y + lay.t(null) + 23} text-anchor="end" style="fill:var(--home)">${l.wrf ? html`<tspan style="fill:var(--mac)">Mac</tspan><tspan style="fill:var(--faint)"> · </tspan>Tower` : l.feed ? l.feed.maker.split(" · ").map((m, i) => html`${i ? html`<tspan style="fill:var(--faint)"> · </tspan>` : null}<tspan style=${`fill:${m === "Tower" ? "var(--home)" : m === "Mac" ? "var(--mac)" : m === "GitHub" ? "var(--muted)" : "var(--accent)"}`}>${m}</tspan>`) : "Tower"}${lay.kt > 1 ? " ×" + lay.kt : ""}</text>
      <circle cx="8" cy=${y + 15} r="4" fill=${dot}/>
      <text class="lname" x="18" y=${y + 19}>${l.label.length > 28 ? l.label.slice(0, 27) + "…" : l.label}</text>`}
      ${l.entries.map((e) => entry(l, e, y, lay))}
      ${l.loose.filter((r) => r.end >= t0 && r.start <= t1).map((r) => { const from = held(r, r.start);
        const yt = y + lay.t(r);
        return html`<g ...${hov({ kind: "tower", l, t: r })}>
        ${from ? html`<rect x=${x(from)} y=${yt + 17} width=${w(from, r.start, 1)} height="6" rx="1" fill="var(--wait)"/>` : null}
        <rect x=${x(r.start)} y=${yt + 17} width=${w(r.start, r.end)} height="6" rx="1" fill=${r.exit !== 0 ? "var(--bad)" : r.host === "mac" ? "var(--mac)" : "var(--home)"} opacity=${r.live ? 1 : 0.55}/>
        ${((p) => p ? html`<rect x=${x(p[0])} y=${yt + 17} width=${w(p[0], p[1], 1)} height="6" rx="1" fill="var(--accent)" opacity=${r.live ? 1 : 0.7}/>` : null)(dlPart(r, r.start, r.end))}</g>`; })}
      ${l.home && l.home.started ? (() => { const je = jobEnd(l.home, now);
        const yt = y + lay.t("home"), ha = l.homeAvail, cur = [...l.entries].reverse().find((o) => o.avail != null && o.avail <= l.home.started + 120);
        const hf = MIX_INPUTS[l.set] ? held(l.home, l.home.started) : ha && l.home.started > ha.avail ? ha.avail : null;
        return html`<g ...${hov({ kind: "running", l, j: l.home, e: ha ? { ...(cur || {}), ...(ha.run ? l.entries.find((o) => o.run === ha.run) : {}), avail: ha.avail, availFrom: ha.availFrom } : cur })}>
          ${hf != null ? html`<rect x=${x(hf)} y=${yt + 17} width=${w(hf, l.home.started, 1)} height="6" rx="1" fill="var(--wait)"/>` : null}<rect x=${x(l.home.started)} y=${yt + 17} width=${w(l.home.started, now, 3)} height="6" rx="1" fill="var(--home)" opacity=${l.home.live ? 1 : 0.55}/>
          ${((p) => p ? html`<rect x=${x(p[0])} y=${yt + 17} width=${w(p[0], p[1], 1)} height="6" rx="1" fill="var(--accent)" opacity=${l.home.live ? 1 : 0.7}/>` : null)(dlPart(l.home, l.home.started, now))}
          ${je ? (je.late ? expect(yt, now, "var(--home)", `${Math.round((now - l.home.started) / 60)} min · over its usual ${Math.round(je.typ / 60)}`, true)
            : expect(yt, je.end, "var(--home)", `${Math.round(je.pct * 100)}% · ~${clock(je.end)}`, false)) : null}</g>`; })() : null}
      ${links(l, y, lay)}
      ${l.feed && l.feed.late && l.feed.last != null ? html`<rect x=${x(Math.max(l.feed.last, t0))} y=${y + lay.t(null) + 17} width=${w(Math.max(l.feed.last, t0), now)} height="6" rx="1" fill="var(--bad)" opacity=".8"><title>${l.label}: nothing new since ${clock(l.feed.last)} (usually ${l.feed.usual})</title></rect>` : null}
      ${/* The next runs: the ▼ hollow where the source should be available, and a dashed ghost of the bar for the lane's
            usual time to published (the owner, 28.10.2026: "lets make better symbol for upcoming scheduled tasks", "Build
            B"; mockup ops-upcoming-runs-2026-10-28; the hollow diamond before). */
        (() => { const out = [], usual = l.towerT ?? l.macT ?? l.ghT, yb = y + lay.t("home") + 17;
        if (l.next) for (let t = l.next; t < t1 && out.length < 30; t += l.cad || 1e9) if (t > now) out.push(html`<g>
          <title>${l.label}: next run expected available about ${clock(t)}${usual ? `, published about ${clock(t + usual)} (usually ${mins(usual)})` : ""}</title>
          <path d=${`M${x(t) - 4} ${y + 1.5} h8 l-4 5 z`} fill="none" stroke="var(--faint)" stroke-width="1.2"/>
          ${usual ? html`<rect x=${x(t)} y=${yb + 0.5} width=${Math.max(x(t + usual) - x(t), 3)} height="5" rx="1" fill="none" stroke="var(--faint)" stroke-dasharray="2 2"/>` : null}</g>`); return out; })()}
      ${compact ? null : html`<text class="ours" x=${X1 + 10} y=${y + 19} style=${"fill:var(--home)" + (l.procOnly ? ";font-style:italic" : "")}>${mins(l.towerT)}</text>
      <text class="ours" x=${X1 + 54} y=${y + 19} style="fill:var(--mac)">${l.macT != null ? mins(l.macT) : "–"}</text>
      <text class="ours" x=${X1 + 98} y=${y + 19} style=${"fill:var(--muted)" + (l.procOnly ? ";font-style:italic" : "")}>${mins(l.ghT)}</text>
      ${l.skipped ? html`<text class="ours" x=${X1 + 142} y=${y + 19} style="fill:var(--bad)">${l.skipped} of ${l.offered}</text>`
        : l.feed && l.feed.late ? html`<text class="ours" x=${X1 + 142} y=${y + 19} style="fill:var(--bad)">${l.feed.last != null ? hrs(now - l.feed.last) : "none"}<title>${l.label}: late, nothing new for ${l.feed.last != null ? hrs(now - l.feed.last) : "ever"}</title></text>` : null}`}
    </g>`; })}
    <line x1=${x(now)} x2=${x(now)} y1=${TOP} y2=${H - 18} stroke="var(--warn)" stroke-width="2"/><text x=${x(now) + 5} y=${H - 6} style="fill:var(--warn)">now ${clock(now)}</text>
  </svg>
    ${pop ? html`<${BarPop} d=${{ ...pop.d, w: wrf, j: (pop.d.kind === "running" && (lanes.find((o) => o.name === pop.d.l.name) || {}).home) || pop.d.j }}
      now=${now} left=${pop.left} top=${pop.top} band=${band} compact=${compact} pinned=${pop.pinned} onClose=${() => setPop(null)} />` : null}</div>`;
}

function RunBars({ l }) {
  const es = l.entries.filter((e) => e.avail != null && !e.skipped && (e.pub || e.tower)).slice(-8);
  if (!es.length) return html`<p class="note" style="margin:0">No runs with an "available" moment yet${l.procOnly ? " (this one is made from our own sets or a source Open-Meteo doesn't have; the chart shows its jobs' own times)" : " (recorded from 21.10.2026 evening)"}.</p>`;
  const top = Math.max(...es.map((e) => Math.max(e.pub ? e.pub - e.avail : 0, e.tower ? e.tower.end - e.avail : 0)), 1800) / 60;
  const max = top <= 60 ? Math.ceil(top / 15) * 15 : Math.ceil(top / 60) * 60;
  const A = 34, B = 350, xx = (m) => A + Math.max(0, Math.min(m, max)) / max * (B - A), H = es.length * 22 + 24;
  return html`<svg viewBox=${`0 0 360 ${H}`} style="width:100%" role="img" aria-label="The last runs, minutes from available">
    ${[0, 0.25, 0.5, 0.75, 1].map((p) => html`<line x1=${xx(p * max)} x2=${xx(p * max)} y1="4" y2=${H - 16} stroke="var(--line)"/><text x=${xx(p * max)} y=${H - 3} text-anchor="middle" style="font:10px var(--mono);fill:var(--muted)">${max > 60 ? (p * max / 60).toFixed(p * max % 60 ? 1 : 0) + (p === 1 ? " h" : "") : Math.round(p * max) + (p === 1 ? " min" : "")}</text>`)}
    ${es.map((e, i) => {
      const y = 6 + i * 22, gw = e.gh ? Math.max(0, e.gh.start - e.avail) / 60 : 0, gt = e.pub ? (e.pub - e.avail) / 60 : null;
      const t = e.tower, tw = t ? Math.max(0, t.start - e.avail) / 60 : 0, tt = t ? (t.end - e.avail) / 60 : null;
      return html`<text x="0" y=${y + 11} style="font:10.5px var(--mono);fill:var(--muted)">${runZ(e.run)}</text>
        ${gt != null && !l.wrf ? html`<rect x=${A} y=${y} width=${xx(Math.min(gw, gt)) - A} height="7" fill="var(--wait)"/><rect x=${xx(Math.min(gw, gt))} y=${y} width=${Math.max(xx(gt) - xx(Math.min(gw, gt)), 1.5)} height="7" fill="var(--gh)"><title>GitHub: waited ${Math.round(gw)} min, published ${Math.round(gt)} min after available</title></rect>` : null}
        ${tt != null ? html`<rect x=${A} y=${y + 9} width=${xx(tw) - A} height="7" fill="var(--wait)"/><rect x=${xx(tw)} y=${y + 9} width=${Math.max(xx(tt) - xx(tw), 1.5)} height="7" fill=${t.ok ? "var(--home)" : "var(--bad)"} opacity=${t.live ? 1 : 0.55}><title>Tower: waited ${Math.round(tw)} min, done ${Math.round(tt)} min after available</title></rect>` : null}`;
    })}</svg>`;
}

function LaneDetail({ l, now, cap, all }) {
  if (!l) return html`<div class="panel"><p class="note" style="margin:0">Tap a lane: its last runs from available, where the time went, what a run costs at home.</p></div>`;
  const last = [...l.entries].reverse().find((e) => e.avail != null && !e.skipped && (e.pub || e.tower));
  const gw = last && last.gh ? Math.max(0, last.gh.start - last.avail) : null;
  const t = last && last.tower;
  const tw = t ? Math.max(0, t.start - last.avail) : null;
  const srcEvery = l.cad ? Math.round(l.cad / 3600) : null;
  const jobs = ((cap && cap.jobs) || []).filter((j) => l.jobs.includes(j.job));
  const makes = (job) => all.filter((x) => x.jobs.includes(job)).length;
  if (l.feed) {
    const fd = l.feed, seen = fd.pubs.filter((p) => p >= now - 12 * 3600).length;
    return html`<div class="panel"><div class="dhead"><b>${l.label}</b><span class=${"tag" + (l.isLive ? " live" : "")}>${fd.maker}</span></div>
      <p class="note" style="margin:6px 0 10px">A live feed: its file on our file store (its publishes as the panel sees them, every 2 minutes), usually every ${fd.usual}, late after ${hrs(fd.limit)}.</p>
      <div class="kv"><span>Last published</span><span style=${fd.late ? "color:var(--bad)" : ""}>${fd.last != null ? `${clock(fd.last)} · ${hrs(now - fd.last)} ago` : "not found"}</span>
        <span>Publishes, last 12 h</span><span>${seen}</span>
        <span>Made by</span><span>${fd.maker}${l.jobs.length ? ` · job ${l.jobs[0]}${l.isLive ? " (live)" : ""}` : ""}</span>
        <span>Next expected</span><span>${l.next ? clock(l.next) : fd.late ? "overdue" : "–"}</span></div></div>
    ${jobs.length ? html`<div class="panel"><h2>A run costs · Tower, 24 h</h2><div class="kv">
      ${jobs.map((j) => html`<span><b>${j.job}</b></span><span>${j.runs} runs</span>
        <span>CPU per run</span><span>${Math.round(j.cpu_h * 60 / Math.max(j.runs, 1))} min</span>
        <span>Peak memory</span><span>${(j.peak_mb / 1000).toFixed(1)} GB</span>
        <span>Downloaded per run</span><span>${(j.net_gb / Math.max(j.runs, 1)).toFixed(2)} GB</span>`)}</div></div>` : null}`;
  }
  return html`<div class="panel"><div class="dhead"><b>${l.label}</b><span class=${"tag" + (l.isLive ? " live" : "")}>${l.wrf ? "at home" : l.jobs.length ? "Tower: " + (l.isLive ? "live" : "shadow") : "GitHub only"}</span></div>
      <p class="note" style="margin:6px 0 10px">${MIX_INPUTS[l.set] ? `Made from ${MIX_INPUTS[l.set].length} of our sets ("available": the first new input published since its run before)` : srcEvery ? `Every ${srcEvery} h at the source` : l.procOnly ? "No source record (made from our own sets or a source Open-Meteo doesn't have)" : "The source's cadence not known yet"}, every ${l.interval} h ours${l.skipped ? ` · ${l.skipped} of ${l.offered} runs skipped in 24 h` : ""}. The last runs, from available:</p>
      <${RunBars} l=${l} />
      <div class="legend"><span style="--c:var(--gh)">GitHub</span><span style="--c:var(--home)">Tower</span><span style="--c:var(--wait)">waiting</span></div></div>
    ${last ? html`<div class="panel"><h2>The last run · ${runZ(last.run)}</h2><div class="kv">
      <span>Available to us</span><span>${clock(last.avail)}${l.wrf ? (last.early ? " (GFS out by then)" : " (GFS out at NOAA)") : ""}</span>
      ${l.wrf ? null : html`<span>GitHub published</span><span>${last.pub ? `${clock(last.pub)} · ${mins(last.pub - last.avail)}` : "–"}</span>
        ${last.gh && last.pub ? html`<span>· waited, then the job</span><span>${mins(gw)} · ${mins(last.pub - last.avail - gw)}</span>` : null}`}
      ${t ? html`<span>${l.wrf ? "WRF ran" : "Tower done"}</span><span>${t.going ? "running" : `${clock(t.end)} · ${mins(t.end - last.avail)}`}</span>
        <span>· waited, then the run</span><span>${mins(tw)} · ${mins(t.end - Math.max(t.start, last.avail))}</span>` : null}
      ${l.wrf && last.pub ? html`<span>Published</span><span>${clock(last.pub)} · ${mins(last.pub - last.avail)}</span>` : null}
      ${l.om && !l.wrf ? html`<span>Open-Meteo had it</span><span>${clock(last.avail)} · 0 min</span>` : null}</div>
      <div class="hint">${l.wrf && l.set !== "wrf-tarifa" ? `Runs once a day after the evening Tarifa run, then waits for GFS if it isn't out yet.`
        : l.wrf ? `WRF starts at fixed times (11:45 and 23:45 Riga) and waits there for GFS, so GFS being available earlier or later moves the wait, not the run.`
        : gw != null && tw != null ? `GitHub waited ${mins(gw)} for its job to start (its cron); at home the job started ${mins(tw)} after the run was available.`
        : gw != null ? `GitHub waited ${mins(gw)} for its job to start (its cron).` : `"Available" is Open-Meteo's record of the run being complete on its open data, where our jobs read it; so Open-Meteo has every run at that same moment.`}</div></div>` : null}
    ${jobs.length ? html`<div class="panel"><h2>A run costs · Tower, 24 h</h2><div class="kv">
      ${jobs.map((j) => html`<span><b>${j.job}</b></span><span>${j.runs} runs${makes(j.job) > 1 ? ` · makes ${makes(j.job)} maps` : ""}</span>
        <span>CPU per run</span><span>${Math.round(j.cpu_h * 60 / Math.max(j.runs, 1))} min</span>
        <span>Peak memory</span><span>${(j.peak_mb / 1000).toFixed(1)} GB</span>
        <span>Downloaded per run</span><span>${(j.net_gb / Math.max(j.runs, 1)).toFixed(2)} GB</span>
        <span>Per day</span><span>${j.cpu_h.toFixed(1)} CPU h · ${j.waited_h.toFixed(1)} h waited</span>`)}</div></div>` : null}
    <div class="panel"><h2>Actions</h2><div class="btns">
      <${ActButton} label="Run the helper now" run=${() => adminPost("run", { what: "helper" })} primary=${true} />
      <${ActButton} label="Rebuild all maps" run=${() => adminPost("run", { what: "rebuild" })} />
    </div><p class="note" style="margin:8px 0 0">Through admin's own routes; each asks once more.</p></div>`;
}

const RANGES = [[6, 3], [12, 6], [24, 12]];

function Pipeline({ maps, fr, f, now, s, cap, ad }) {
  const [sel, setSel] = useState(null);
  const [filter, setFilter] = useState("all");
  // The hours shown, remembered in this browser (the owner, 26.10.2026: "have to save selection 6/12/24h ... so after
  // restart of page its not jump back to default 12"), as the band's pin is.
  const [range, setRangeState] = useState(() => { try { const v = +localStorage.getItem("ops-range"); return RANGES.some((r) => r[0] === v) ? v : 12; } catch (e) { return 12; } });
  const setRange = (v) => { try { localStorage.setItem("ops-range", String(v)); } catch (e) {} setRangeState(v); };
  const all = laneData(maps, fr, f, now, s);
  const both = (l) => l.towerT != null && l.ghT != null && !l.procOnly;
  const pick = { all: () => true, sooner: (l) => both(l) && l.towerT < l.ghT, later: (l) => both(l) && l.towerT >= l.ghT,
    skip: (l) => l.skipped > 0, home: (l) => l.isLive || !!l.home, bad: (l) => l.status === "bad" };
  const lanes = all.filter(pick[filter]);
  // The live feeds: each one's publishes, its age from the newest (live, each second), late past its limit.
  const feeds = ((ad && ad.feeds) || []).filter((x) => FEED_ROWS[x.key]).map((x) => {
    const [maker, every, limit] = FEED_ROWS[x.key], pubs = ((ad && ad.pubs) || {})[x.key] || [];
    const last = pubs.length ? pubs[pubs.length - 1] : x.age != null ? (ad.t || now) - x.age : null;
    return { ...x, maker, every, limit, pubs, last, late: last == null || now - last > limit };
  }).filter((x) => filter === "all" || (filter === "bad" && x.late) || (filter === "home" && ((f && f.live) || []).includes(FEED_JOB[x.key])));
  // As lanes of their own kind (the owner, 26.10.2026: "same style as other rows of table? Highlight on hover, show line
  // with works and open detailed window"): made from our own work only (procOnly), its job's runs as the bars, its
  // publishes as the marks, late a red bar from the last one to now.
  const day0 = now - 24 * 3600, liveJobs = new Set((f && f.live) || []);
  const feedLanes = feeds.map((fd) => {
    const job = FEED_JOB[fd.key], runs = job ? ((s && s.home) || []).filter((r) => r.job === job) : [];
    return { name: "feed:" + fd.key, label: fd.name, set: fd.key, group: "Live feeds", feed: fd, jobs: job ? [job] : [],
      entries: fd.pubs.map((p) => ({ run: p, avail: null, pub: p })), towerRuns: runs, loose: runs, procOnly: true, homeOnly: true,
      isLive: liveJobs.has(job), status: fd.late ? "bad" : "ok", skipped: 0, offered: 0, wrf: false, home: null, homeAvail: null,
      towerT: med(runs.filter((r) => r.exit === 0 && r.end >= day0).map((r) => r.end - r.start)), macT: null, ghT: null,
      cad: null, next: fd.late || fd.last == null ? null : fd.last + fd.every, interval: fd.every / 3600, delay: 0 };
  });
  const picked = all.find((l) => l.name === sel) || feedLanes.find((l) => l.name === sel);
  // A tapped lane's detail sits under the chart: brought into view.
  const choose = (name) => { setSel(name); setTimeout(() => { const d = document.getElementById("lanedetail"); if (d) d.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, 50); };
  // Today's summary: our time on each machine, GitHub's waiting share, runs skipped, the Tower against GitHub.
  const day = all.filter((l) => !l.procOnly).flatMap((l) => l.entries.filter((e) => e.avail != null && e.avail >= now - 24 * 3600));
  const tMed = med(day.map((e) => e.towerTime).filter((v) => v != null)), gMed = med(day.map((e) => e.ghTime).filter((v) => v != null));
  const gx = day.filter((e) => e.gh && e.pub && e.ghTime > 0);
  const waitShare = gx.length ? gx.reduce((a, e) => a + Math.min(Math.max(0, e.gh.start - e.avail), e.ghTime), 0) / gx.reduce((a, e) => a + e.ghTime, 0) : null;
  const skipped = all.reduce((a, l) => a + l.skipped, 0), offered = all.reduce((a, l) => a + l.offered, 0);
  const compared = all.filter(both), sooner = compared.filter((l) => l.towerT < l.ghT).length;
  const [back, ahead] = RANGES.find((r) => r[0] === range);
  // The machine band (/api/band): when the Pipeline opens, every 2 minutes, and for each range.
  const [band, setBand] = useState(null);
  useEffect(() => {
    let stop = false;
    const load = () => fetch(`/api/band?hours=${back}`, { cache: "no-store" }).then((r) => r.json()).then((d) => { if (!stop) setBand(d); }).catch(() => {});
    load();
    const id = setInterval(load, 120000);
    return () => { stop = true; clearInterval(id); };
  }, [back]);
  const chips = [["all", `All ${all.length}`], ["sooner", `Tower sooner ${sooner}`], ["later", `GitHub sooner ${compared.length - sooner}`],
    ["skip", `Skipping runs ${all.filter((l) => l.skipped).length}`], ["home", `At home ${all.filter(pick.home).length}`], ["bad", `Behind ${all.filter(pick.bad).length}`]];
  return html`<div class="panel pstrip">
      <div><span class="v" style="color:var(--home)">${mins(tMed)}</span><span class="l">our time today, Tower (median)</span></div>
      <div><span class="v">${mins(gMed)}</span><span class="l">GitHub</span></div>
      ${((l) => l && l.macT != null ? html`<div><span class="v" style="color:var(--mac)">${mins(l.macT)}</span><span class="l">WRF, Mac mini</span></div>` : null)(lanes.find((o) => o.wrf))}
      <div><span class="v">${waitShare == null ? "–" : Math.round(waitShare * 100) + "%"}</span><span class="l">of GitHub's time is waiting for its job to start</span></div>
      <div><span class="v" style=${skipped ? "color:var(--warn)" : ""}>${skipped}</span><span class="l">runs skipped today of ${offered} offered</span></div>
      <div><span class="v">${sooner} of ${compared.length}</span><span class="l">maps the Tower makes sooner</span></div></div>
    <div class="pipe" style="margin-top:16px">
    <section class="panel" aria-label="Pipeline">
      ${!maps ? html`<h2>Every map: from available to published · last ${back} h and next ${ahead} · ${zoneName()} time</h2><p class="empty">Reading admin's map history…</p>` : html`
      <${Lanes} lanes=${[...lanes, ...feedLanes]} now=${now} sel=${sel} setSel=${choose} back=${back} ahead=${ahead} band=${band} slots=${f && f.slots} wrf=${f && f.mac && f.mac.wrf && (f.mac.wrf.running || f.mac.wrf.waiting) ? { ...(f.wrf || {}), ...f.mac.wrf } : f && f.wrf}
        head=${html`<h2>Every map: from available to published · last ${back} h and next ${ahead} · ${zoneName()} time</h2>
        <div class="filters">${RANGES.map(([b]) => html`<span class=${"chip" + (range === b ? " on" : "")} onClick=${() => setRange(b)}>${b} h</span>`)}<span style="width:10px"></span>
          ${chips.map(([k, label]) => html`<span class=${"chip" + (filter === k ? " on" : "")} onClick=${() => setFilter(k)}>${label}</span>`)}</div>`} />
      <div class="legend"><span style="--c:var(--wrf)">CPU: WRF</span><span style="--c:var(--home)">CPU: jobs · jobs running</span><span style="--c:var(--mac)">Mac mini: its other work</span><span style="--c:var(--wait)">jobs waiting · waiting to start</span><span style="--c:var(--accent)">memory used (dashed: the jobs' budget) · a run's downloading</span><span style="--c:var(--net)">network in (dashed: out)</span><span style="--c:var(--room)">waiting with room (CPU &lt; 50%, &gt; 6 GB free)</span></div>
      <div class="legend"><span class="t">▼ run available to us (Open-Meteo's record; a mix: a new input published; a line down: its bars in a lane with more rows)</span><span style="--c:var(--gh)">GitHub (upper bar; for maps made at home only where it stood in)</span><span style="--c:var(--home)">Tower (lower bar; faint: shadow, not published)</span><span style="--c:var(--mac)">Mac mini (lower bar)</span>
        <span style="--c:var(--wait)">waiting to start</span><span style="--c:var(--ok)">published</span><span class="t" style="color:var(--bad)">× a source run nobody took</span><span class="t">▽ ┄ next run expected: available, then its usual time to published</span><span class="t">right: our time, median of 24 h (italic: the job's own time, no "available" moment)</span></div>
      <p class="note" style="margin:8px 0 0">Each bar starts when the run was complete on Open-Meteo's open data, where our jobs read it, and ends when riders could see it. The maker's own time before that is left out. Sooner is better. "Available" is recorded for every model from 21.10.2026 evening; older runs start at GitHub's job.</p>`}
    </section>
    <aside class="detail" id="lanedetail">${html`<${LaneDetail} l=${picked} now=${now} cap=${cap} all=${all} />`}</aside>
  </div>
  <div style="margin-top:16px"><${Freshness} fr=${fr} now=${now} /></div>`;
}

