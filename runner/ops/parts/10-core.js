const GB = (b) => (b / 1e9).toFixed(1);
const MBs = (v) => v >= 20 ? Math.round(v) : v >= 1 ? v.toFixed(1) : v > 0 ? v.toFixed(2) : "0";
const pad = (n) => String(n).padStart(2, "0");
const dur = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, r = s % 60; return h ? `${h}:${pad(m)}:${pad(r)}` : `${m}:${pad(r)}`; };
const prioClass = (p) => p === 1 ? "p1" : p === 2 ? "p2" : "p3";
let ZONE = "Europe/Riga";
try { ZONE = localStorage.getItem("ops-zone") || ZONE; } catch (e) {}
const clock = (t, sec) => t ? new Date(t * 1000).toLocaleTimeString("en-GB", { timeZone: ZONE, hour: "2-digit", minute: "2-digit", second: sec ? "2-digit" : undefined }) : "–";
const zoneName = () => ZONE === "UTC" ? "UTC" : "Riga";
const base = (u) => { try { const p = new URL(u); const parts = p.pathname.split("/").filter(Boolean); return p.hostname + "/…/" + parts.slice(-2).join("/"); } catch (e) { return u; } };

function Spark({ values, w = 70, h = 18 }) {
  if (!values || values.length < 2) return html`<svg width=${w} height=${h}></svg>`;
  const max = Math.max(...values, 1);
  const d = values.map((v, i) => `${i ? "L" : "M"}${(i / (values.length - 1) * w).toFixed(1)} ${(h - v / max * (h - 2) - 1).toFixed(1)}`).join(" ");
  return html`<svg viewBox="0 0 ${w} ${h}" width=${w} height=${h}><path d=${d} fill="none" stroke="var(--accent)" stroke-width="1.5"/></svg>`;
}

function Top({ f, zone, setZone, tab, fr, cap }) {
  const now = Date.now() / 1000;
  const waitingMem = (f.waiting || []).filter((w) => /^waits for (memory|WRF)/.test(w.why || "")).length;
  const busy = (f.jobs || []).length;
  const stale = f.live_t && now - f.live_t > 180;
  const beatAge = f.beat ? now - f.beat : null;
  const behind = fr && fr.counts ? fr.counts.bad : 0;
  return html`<header class="top"><div class="wrap"><div class="row1">
    <div class="brand"><span class="dot" aria-hidden="true" style=${stale ? "background:var(--bad)" : ""}></span>MeteoGust ops
      <small>${f.host || "…"} · ${(f.live || []).length ? `${f.live.length} live` : "shadow"}</small></div>
    <div class="pills">
      ${behind ? html`<a class="pill bad" href="#freshness" style="text-decoration:none">${behind} model${behind > 1 ? "s" : ""} behind</a>` : null}
      ${f.wrf && f.wrf.running
        ? html`<span class="pill wrf">WRF ${f.wrf.region || ""} running · ${dur(now - (f.wrf.started || now))}</span>`
        : f.wrf && f.wrf.waiting
        ? html`<span class="pill">WRF waiting for GFS · since ${clock(f.wrf.started)}</span>`
        : html`<span class="pill">WRF idle · next ${clock((f.wrf && f.wrf.next || [])[0])}</span>`}
      <span class="pill">${busy} of ${f.slots || "?"} job slots${f.wrf && f.wrf.running ? " (1 big job during WRF)" : ""}</span>
      ${waitingMem ? html`<span class="pill warn">${waitingMem} waiting for memory</span>` : null}
      ${stale ? html`<span class="pill bad">scheduler quiet for ${dur(now - f.live_t)}</span>` : null}
      <span class="pill">heartbeat ${beatAge == null ? "not sent" : dur(beatAge) + " ago"}</span>
    </div>
    <div class="clock"><b>${clock(now, true)}</b> ${zoneName()}
      <button class="tbtn" id="zone" onClick=${() => setZone(zone === "UTC" ? "Europe/Riga" : "UTC")}>show ${zone === "UTC" ? "Riga" : "UTC"}</button></div>
  </div><${Tabs} tab=${tab} fr=${fr} cap=${cap} /></div></header>`;
}

function Machine({ f }) {
  const cores = f.cores || [];
  const total = f.mem_total || 1, used = total - (f.mem_avail || 0);
  const conts = (f.containers || []).filter((c) => c.kind !== "other" || c.mem > 2e8);
  const sum = conts.reduce((a, c) => a + c.mem, 0);
  const segs = [...conts.map((c) => ({ name: c.kind === "wrf" ? "WRF" : c.name, mem: c.mem, color: c.kind === "wrf" ? "var(--wrf)" : c.kind === "job" ? "var(--accent)" : "var(--faint)" })),
    { name: "system and caches", mem: Math.max(used - sum, 0), color: "color-mix(in srgb, var(--faint) 60%, var(--panel))" }];
  const hist = (f.net_hist || []).map((h) => h[1]);
  const max = Math.max(117, ...hist, 1);
  const w = 300, hh = 64;
  const pts = hist.map((v, i) => [hist.length > 1 ? i / (hist.length - 1) * w : 0, hh - v / max * (hh - 4)]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
  const limitY = hh - 117 / max * (hh - 4);
  return html`<section class="panel s12" aria-label="The machine"><div class="machine">
    <div><h2>Threads (${cores.length})</h2>
      <div class="cores" style=${`grid-template-columns:repeat(${Math.min(cores.length || 10, 10)},1fr)`}>
        ${cores.map((v) => html`<div class=${"core" + (v < 5 ? " idle" : "")}><i style=${`height:${Math.max(v, 2)}%`}></i></div>`)}
      </div>
      <div class="legend"><span style="--c:var(--accent)">busy share of each thread, last 2 s</span></div></div>
    <div><h2>Memory</h2>
      <div class="memline"><span class="big">${GB(used)} <small>of ${GB(total)} GB</small></span>
        <span class="mono" style="font-size:13px;color:var(--muted)">jobs' budget ${f.budget ? (f.budget / 1000).toFixed(1) : "?"}</span></div>
      <div class="mem" role="img" aria-label="Memory by user">${segs.filter((s) => s.mem > 0).map((s) => html`<div title=${`${s.name} ${GB(s.mem)} GB`} style=${`width:${(s.mem / total * 100).toFixed(2)}%;background:${s.color}`}></div>`)}</div>
      <div class="legend">${segs.filter((s) => s.mem > 1e8).map((s) => html`<span style=${`--c:${s.color}`}>${s.name} ${GB(s.mem)}</span>`)}</div>
      <div class="memnote">Reserved for running jobs: <b class="num">${f.reserved != null ? (f.reserved / 1000).toFixed(1) : "?"} GB</b>${f.wrf && f.wrf.running ? " (WRF's 9 GB off the budget while it runs)" : ""}.</div></div>
    <div class="net"><h2>Network</h2>
      <div class="netnums"><span class="big">${MBs(f.net_in || 0)} <small>MB/s in</small></span><span class="big">${MBs(f.net_out || 0)} <small>MB/s out</small></span></div>
      <svg viewBox="0 0 300 64" preserveAspectRatio="none" aria-label="Download speed, last minutes">
        <line x1="0" x2="300" y1=${limitY} y2=${limitY} stroke="var(--faint)" stroke-dasharray="3 4" stroke-width="1" vector-effect="non-scaling-stroke"/>
        ${hist.length > 1 ? html`<path d=${line + " L300 64 L0 64 Z"} fill="var(--accent-soft)"/><path d=${line} fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke"/>` : null}
      </svg>
      <div class="legend"><span style="--c:var(--accent)">in, last ${Math.round(hist.length * 2 / 60) || "<1"} min</span><span style="--c:var(--faint)">line limit ~117 MB/s</span></div></div>
  </div></section>`;
}

function Log({ job }) {
  const [lines, setLines] = useState([]);
  const box = useRef(null);
  useEffect(() => {
    const es = new EventSource("/api/log?job=" + encodeURIComponent(job));
    const got = [];
    es.addEventListener("line", (e) => { got.push(JSON.parse(e.data).text); if (got.length > 200) got.shift(); setLines([...got]); });
    return () => es.close();
  }, [job]);
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [lines]);
  return html`<div class="log" ref=${box}>${lines.length ? lines.map((l) => html`<div>${l}</div>`) : html`<div>Reading the log…</div>`}</div>`;
}

function JobCard({ j, now, open, toggle }) {
  const el = j.started ? now - j.started : null;
  const typ = j.typical;
  const pct = el != null && typ ? Math.min(el / (typ * 60), 1) : null;
  const over = el != null && typ && el > typ * 60 * 1.2;
  const d = j.download;
  const speed = j.net_in || 0;
  return html`<div class="job" tabindex="0" onClick=${toggle} onKeyDown=${(e) => e.key === "Enter" && toggle()}>
    <div class="jhead"><span class=${"prio " + prioClass(j.priority)}>P${j.priority}</span><span class="jname">${j.job}</span>
      <span class=${"tag" + (j.live ? " live" : "")}>${j.live ? "live" : "shadow"}</span>
      <span class="jstats"><span><b>${el != null ? dur(el) : "–"}</b></span><span>CPU <b>${j.cpu != null ? j.cpu + "%" : "–"}</b></span><span><b>${j.mem != null ? GB(j.mem) : "–"}</b> GB</span></span></div>
    <div class="what">${j.source ? `source run ${new Date(j.source * 1000).toISOString().slice(8, 13).replace("T", " ")}Z · ` : ""}${typ ? `usually ${typ} min` : "first runs: no usual time yet"}${over ? html` · <b class="warn">running long</b>` : pct != null ? html` · <b>~${Math.round(pct * 100)}% · ends ~${clock(j.started + typ * 60)}</b>` : ""} · plans for ${j.need ? (j.need / 1000).toFixed(1) : "?"} GB</div>
    ${pct != null ? html`<div class="bar"><i class=${over ? "over" : ""} style=${`width:${(pct * 100).toFixed(1)}%`}></i></div>` : null}
    <div class="dl"><span>↓</span><span class="file">${d ? `${d.host} · ${base(d.url)}${d.done ? " · done" : ""}` : "no download right now"}</span>
      <${Spark} values=${j.speeds} /><span class="speed">${MBs(speed)} MB/s</span></div>
    ${open ? html`<${Log} job=${j.job} />` : html`<div class="logline">tap for the live log</div>`}
  </div>`;
}

function WrfCard({ w, now, open, toggle }) {
  return html`<div class="job wrfjob" tabindex="0" onClick=${toggle} onKeyDown=${(e) => e.key === "Enter" && toggle()}>
    <div class="jhead"><span class="prio pw">WRF</span><span class="jname">WRF ${w.region || ""} 1 km</span>
      <span class="jstats"><span><b>${dur(now - (w.started || now))}</b></span><span>CPU <b>${w.cpu != null ? w.cpu + "%" : "–"}</b></span><span><b>${w.mem != null ? GB(w.mem) : "–"}</b> GB</span></span></div>
    <div class="what">${w.box ? `1 km box ${w.box[0]} to ${w.box[1]} UTC · model time ${w.model_time || "–"}` : "preparing (GFS, the boxes)"}${(() => { const x = wrfEnd(w, now); return x ? html` · <b>${Math.round(x.pct * 100)}% · ends ~${clock(x.end)}</b>` : null; })()}</div>
    ${w.progress != null ? html`<div class="bar"><i style=${`width:${(w.progress * 100).toFixed(1)}%`}></i></div>` : null}
    ${open ? html`<${Log} job="wrf" />` : html`<div class="logline">${w.last_line || ""}</div>`}
  </div>`;
}

function Running({ f, now }) {
  const [open, setOpen] = useState(null);
  const t = (k) => () => setOpen(open === k ? null : k);
  const jobs = [...(f.jobs || [])].sort((a, b) => a.priority - b.priority);
  return html`<section class="panel s8" aria-label="Running now"><h2>Running now</h2><div class="jobs">
    ${f.wrf && f.wrf.running ? html`<${WrfCard} w=${f.wrf} now=${now} open=${open === "wrf"} toggle=${t("wrf")} />` : null}
    ${jobs.map((j) => html`<${JobCard} key=${j.job} j=${j} now=${now} open=${open === j.job} toggle=${t(j.job)} />`)}
    ${!jobs.length && !(f.wrf && f.wrf.running) ? html`<p class="empty">Nothing running: every job is waiting for its next run.</p>` : null}
  </div></section>`;
}

function Next({ f }) {
  // During WRF a dozen can wait at once: the first 8 by priority, the rest named in one line.
  const allWaiting = f.waiting || [];
  const waiting = allWaiting.slice(0, 8);
  const moreWaiting = allWaiting.slice(8);
  const next = (f.next || []).filter((n) => n.at).slice(0, 10);
  const later = (f.next || []).filter((n) => !n.at);
  const wnext = (f.wrf && f.wrf.next) || [];
  return html`<section class="panel s4" aria-label="What's next"><h2>Next, and why</h2><div class="next">
    ${waiting.map((w) => html`<div class="nrow"><span class=${"prio " + prioClass(w.priority)}>P${w.priority}</span>
      <span class="n">${w.job}${(w.why || "").startsWith("waits for WRF") ? html`<span class="chip wrf">after WRF</span>` : (w.why || "").startsWith("waits for memory") ? html`<span class="chip mem">memory</span>` : (w.why || "").startsWith("priority 3") ? html`<span class="chip hold">held</span>` : null}</span>
      <span class="t now">due</span><span class="why">${w.source ? w.source + ": " : ""}${w.why}</span></div>`)}
    ${moreWaiting.length ? html`<div class="nrow"><span></span><span class="why" style="grid-column:2/4">Also due and waiting: ${moreWaiting.map((w) => w.job).join(", ")}</span></div>` : null}
    ${next.map((n) => html`<div class="nrow"><span class=${"prio " + prioClass(n.priority)}>P${n.priority}</span><span class="n">${n.job}</span>
      <span class="t">${n.why.startsWith("every") ? "" : "~"}${clock(n.at)}</span><span class="why">${n.why}</span></div>`)}
    ${wnext[0] ? html`<div class="nrow"><span class="prio pw">WRF</span><span class="n">WRF Tarifa</span><span class="t">${clock(wnext[0])}</span>
      <span class="why">From the newest GFS. Job slots halve and their CPU share drops while it runs.</span></div>` : null}
    ${later.length ? html`<div class="nrow"><span></span><span class="why" style="grid-column:2/4">Not known yet: ${later.map((n) => n.job).join(", ")}</span></div>` : null}
  </div></section>`;
}

function Timeline({ s, now }) {
  if (!s || !s.items) return html`<section class="panel s12"><h2>Timeline</h2><p class="empty">Loading…</p></section>`;
  const X0 = 150, X1 = 1225, top = 38, lh = 30;
  const t0 = s.from, t1 = s.to;
  const x = (t) => X0 + (t - t0) / (t1 - t0) * (X1 - X0);
  const lanes = s.lanes;
  const hours = [];
  const first = Math.ceil(t0 / 3600) * 3600;
  for (let t = first; t <= t1; t += 3600) hours.push(t);
  const color = (k) => k === "wrf" || k === "wrfplan" ? "var(--wrf)" : k === "bad" ? "var(--bad)" : k === "plan" ? "var(--line)" : "var(--accent)";
  const H = top + lanes.length * lh + 24;
  return html`<section class="panel s12" aria-label="Timeline"><h2>Last 12 hours and next 6 · ${zoneName()} time</h2>
    <div class="tlwrap"><svg class="tl" viewBox="0 0 1240 ${H}" role="img" aria-label="Timeline of job runs">
      ${hours.map((t, i) => html`<line x1=${x(t)} x2=${x(t)} y1=${top - 6} y2=${top + lanes.length * lh} stroke="var(--line)" stroke-width="1"/>
        ${i % 2 === 0 ? html`<text x=${x(t)} y=${top - 12} text-anchor="middle">${clock(t)}</text>` : null}`)}
      ${(s.arrivals || []).map((a) => html`<path d=${`M${x(a.t) - 4} ${top - 4} L${x(a.t) + 4} ${top - 4} L${x(a.t)} ${top + 2} Z`} fill="var(--muted)"><title>${a.label} arrived ${clock(a.t)}</title></path>`)}
      ${lanes.map((name, i) => html`<text class="lane" x="0" y=${top + i * lh + 19}>${name}</text>`)}
      ${s.items.map((it) => {
        const i = lanes.indexOf(it.lane); if (i < 0) return null;
        const xa = x(Math.max(it.a, t0)), xb = Math.max(x(Math.min(it.b, t1)), xa + 3);
        return html`<rect x=${xa.toFixed(1)} y=${top + i * lh + 7} width=${(xb - xa).toFixed(1)} height="16" rx="3" fill=${it.lane === "WRF" && it.kind === "run" ? "var(--wrf)" : color(it.kind)} opacity=${it.kind === "wrfplan" ? 0.35 : 1}
          stroke=${it.kind === "run" ? "var(--ink)" : "none"} stroke-width="1.5"><title>${it.job} ${clock(it.a)}–${clock(it.b)}</title></rect>`;
      })}
      <line x1=${x(now)} x2=${x(now)} y1=${top - 8} y2=${top + lanes.length * lh + 4} stroke="var(--warn)" stroke-width="2"/>
      <text x=${x(now) + 6} y=${top + lanes.length * lh + 16} style="fill:var(--warn)">now ${clock(now)}</text>
    </svg></div>
    <div class="legend"><span style="--c:var(--accent)">ran</span><span style="--c:var(--bad)">failed</span><span style="--c:var(--wrf)">WRF</span><span style="--c:var(--line)">expected</span><span>▾ a model's new run complete</span></div>
  </section>`;
}

function Sources({ f, s }) {
  const hosts = (s && s.hosts) || {};
  const now = f.sources_now || {};
  const names = [...new Set([...Object.keys(hosts), ...Object.keys(now)])]
    .sort((a, b) => ((hosts[b] || {}).bytes || 0) - ((hosts[a] || {}).bytes || 0));
  return html`<section class="panel s5" aria-label="Downloads by source"><h2>Downloads by source · today (UTC)</h2>
    ${names.length ? html`<div class="tablewrap"><table><thead><tr><th>Source</th><th class="r">Now</th><th class="r">Best today</th><th class="r">Today</th></tr></thead>
      <tbody>${names.map((n) => html`<tr><td style="overflow-wrap:anywhere">${n}</td><td class="r num">${MBs((now[n] || 0) / 1e6)} MB/s</td>
        <td class="r num">${(hosts[n] || {}).best ? MBs(hosts[n].best / 1e6) + " MB/s" : "–"}</td><td class="r num">${GB((hosts[n] || {}).bytes || 0)} GB</td></tr>`)}</tbody></table></div>`
      : html`<p class="empty">No downloads reported yet today.</p>`}
  </section>`;
}

function Runs({ s }) {
  const runs = ((s && s.runs) || []).slice(0, 12);
  return html`<section class="panel s7" aria-label="Recent runs"><h2>Recent runs</h2>
    ${runs.length ? html`<div class="tablewrap"><table><thead><tr><th>Job</th><th>Ended</th><th class="r">Took</th><th class="r">On GitHub</th><th>Result</th></tr></thead>
      <tbody>${runs.map((r) => {
        const factor = r.github && r.took ? r.github / r.took : null;
        const ok = r.exit === 0;
        const why = r.exit === -9 || r.exit === 137 || r.exit === 247 ? "stopped: out of memory" : `stopped (exit ${r.exit})`;
        return html`<tr><td>${r.job}</td><td class="num">${clock(r.end)}</td><td class="r num">${r.took} min</td>
          <td class="r num">${r.github ? html`${r.github} min ${factor >= 1.15 ? html`<span class="faster">${factor.toFixed(1)}×</span>` : factor < 0.87 ? html`<span class="slower">slower</span>` : ""}` : "–"}</td>
          <td class=${ok ? "ok" : "bad"}>${ok ? "done" : why}${r.peak_mb ? ` · ${(r.peak_mb / 1000).toFixed(1)} GB` : ""}${r.net_mb ? ` · ${(r.net_mb / 1000).toFixed(1)} GB in` : ""}</td></tr>`;
      })}</tbody></table></div>` : html`<p class="empty">No runs recorded yet.</p>`}
  </section>`;
}

// MARK: v2 tabs (the owner, 21.10.2026: Freshness first, then Now, Capacity, Admin; docs/mockups/ops-panel-v2-2026-10-21.html)

const TABS = ["freshness", "now", "capacity", "admin"];
const runZ = (t) => t ? new Date(t * 1000).toISOString().slice(11, 13) + "Z" : "–";
const dayRunZ = (t, ref) => {
  if (!t) return "–";
  const d = new Date(t * 1000).toISOString();
  return (ref && Math.abs(ref - t) > 20 * 3600 ? d.slice(8, 10) + " " : "") + d.slice(11, 13) + "Z";
};
const hrs = (s) => s == null ? "–" : s >= 3600 ? (s / 3600).toFixed(1) + " h" : Math.round(s / 60) + " min";
const STATUS = { ok: "on time", proc: "processing", wait: "due", bad: "behind", unknown: "no record" };

function Tabs({ tab, fr, cap }) {
  const bad = fr && fr.counts ? fr.counts.bad : 0;
  const capWarn = cap && (cap.mem_full_h > 1 || cap.waited_h > 2);
  return html`<nav class="tabs" aria-label="Sections">
    ${TABS.map((t) => html`<a href=${"#" + t} class=${tab === t ? "on" : ""}>${t[0].toUpperCase() + t.slice(1)}
      ${t === "freshness" && bad ? html`<span class="badge">${bad}</span>` : null}
      ${t === "capacity" && capWarn ? html`<span class="badge warn">!</span>` : null}</a>`)}
  </nav>`;
}

function FreshStrip({ row, now }) {
  const w = 260, h = 22, from = now - 24 * 3600;
  const x = (t) => Math.max(0, Math.min(w, (t - from) / (24 * 3600) * w));
  const allow = Math.max((row.usual_ours || 3 * 3600) - (row.usual_maker || 0), 1800) * 1.5;
  const strip = row.strip || [];
  return html`<svg class="strip" viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <line x1="0" x2=${w} y1=${h / 2} y2=${h / 2} stroke="var(--line)"/>
    ${strip.map(([run, done, pub], i) => {
      const last = i === strip.length - 1;
      const end = pub || (last ? now : Math.min(now, done + allow * 2));
      const color = pub ? (pub - done <= allow ? "var(--ok)" : "var(--warn)") : last ? "var(--accent)" : "var(--bad)";
      const a = x(done), b = x(end);
      return html`<rect x=${a.toFixed(1)} y=${h / 2 - 4} width=${Math.max(b - a, 2).toFixed(1)} height="8" rx="2" fill=${color}><title>${runZ(run)}: complete ${clock(done)}, ours ${pub ? clock(pub) : "not yet"}</title></rect>`;
    })}
    <line x1=${w - 1} x2=${w - 1} y1="2" y2=${h - 2} stroke="var(--warn)" stroke-width="2"/>
  </svg>`;
}

function behindText(r) {
  if (!r.behind_runs) return "–";
  return `${r.behind_runs} run${r.behind_runs > 1 ? "s" : ""}${r.waited != null ? " · " + hrs(r.waited) : ""}`;
}

function usualText(r) {
  const m = r.usual_maker != null ? `maker +${hrs(r.usual_maker)}` : null;
  const o = r.usual_ours != null ? `ours +${hrs(r.usual_ours)}` : null;
  return [m, o].filter(Boolean).join(" · ") || "–";
}

function oursText(r) {
  const both = r.ours_spots && r.ours_map && r.ours_spots !== r.ours_map;
  return both ? `spots ${runZ(r.ours_spots)} · map ${runZ(r.ours_map)}` : "";
}

function Freshness({ fr, now }) {
  if (!fr || !fr.rows) return html`<p class="empty">Reading the makers' and our runs…</p>`;
  const groups = [];
  fr.rows.forEach((r) => { if (!groups.length || groups[groups.length - 1].name !== r.group) groups.push({ name: r.group, rows: [] }); groups[groups.length - 1].rows.push(r); });
  const c = fr.counts || {};
  return html`<div class="grid">
    <div class="panel s12"><div class="ftiles">
      <div class="ftile ok"><div class="v">${c.ok || 0}</div><div class="l">on time: our newest run is the maker's newest</div></div>
      <div class="ftile proc"><div class="v">${c.proc || 0}</div><div class="l">processing: a newer run is being made now</div></div>
      <div class="ftile wait"><div class="v">${c.wait || 0}</div><div class="l">due: a newer run is out, ours within its usual lag or queued</div></div>
      <div class="ftile bad"><div class="v">${c.bad || 0}</div><div class="l">behind: a newer run sits unprocessed past its usual lag</div></div>
    </div></div>
    <div class="panel s12"><h2>Each model: the maker's newest run against ours · ${zoneName()} time</h2>
      <div class="fcards">${groups.map((g) => html`<div class="fcard group">${g.name}</div>${g.rows.map((r) => html`<div class="fcard">
        <div class="h"><b>${r.label}</b><span class=${"where" + (r.where === "Home" ? " home" : "")}>${r.where}</span>
          <span class=${"st " + r.status}>${STATUS[r.status]}${r.behind_runs ? " · " + behindText(r) : ""}</span></div>
        <div class="t">maker ${dayRunZ(r.maker_run, now)}${r.maker_done ? " " + clock(r.maker_done) : ""} · ours ${dayRunZ(r.ours, now)}${r.published ? " " + clock(r.published) : ""}</div>
        ${r.processing || oursText(r) ? html`<div class="whytxt">${[r.processing, oursText(r)].filter(Boolean).join(" · ")}</div>` : null}</div>`)}`)}</div>
      <div class="tablewrap" id="freshtable"><table class="fresh-table">
        <thead><tr><th>Model</th><th>Runs</th><th>Maker's newest</th><th>Ours, published</th><th>Status</th><th class="r">Behind</th><th>Usual, from the run's start</th><th>Last 24 h: maker complete → ours</th></tr></thead>
        <tbody>${groups.map((g) => html`<tr class="group"><td colspan="8">${g.name}</td></tr>${g.rows.map((r) => html`<tr>
          <td><b>${r.label}</b>${r.processing ? html`<span class="sub">${r.processing}</span>` : null}</td>
          <td class="whytxt">${r.every === 1 ? "hourly" : "every " + r.every + " h"}</td>
          <td class="num">${dayRunZ(r.maker_run, now)}<span class="sub">${r.maker_done ? "complete " + clock(r.maker_done) : r.maker_run ? "" : "not in Open-Meteo's record"}</span></td>
          <td class="num">${dayRunZ(r.ours, now)}<span class="sub">${r.published ? clock(r.published) : ""}<span class=${"where" + (r.where === "Home" ? " home" : "")}>${r.where}</span>${oursText(r) ? html`<br/>${oursText(r)}` : null}</span></td>
          <td><span class=${"st " + r.status}>${STATUS[r.status]}</span></td>
          <td class="r num" style=${r.status === "bad" ? "color:var(--bad)" : ""}>${behindText(r)}</td>
          <td class="num whytxt">${usualText(r)}</td>
          <td><${FreshStrip} row=${r} now=${now} /></td></tr>`)}`)}</tbody></table></div>
      <div class="legend"><span style="--c:var(--ok)">published within its usual lag</span><span style="--c:var(--warn)">later than usual</span><span style="--c:var(--bad)">not published</span><span style="--c:var(--accent)">in progress</span></div>
      <p class="note">Maker's newest: Open-Meteo's open-data record (the makers' release or a little after), at the run hours our job takes. Ours: the spot forecasts' and the map's newest run, the older of the two. "Usual" is from the run's start: how long the maker takes, and how long until ours is published. The strips fill in from 21.10.2026 (the panel's own record).</p>
    </div>
    <div class="panel s12"><h2>Products riders see, made from our sets</h2>
      <div class="tablewrap"><table><thead><tr><th>Product</th><th>Made</th><th>Inputs newer since</th><th>Now</th></tr></thead>
      <tbody>${(fr.products || []).map((p) => html`<tr><td><b>${p.label}</b></td><td class="num">${p.updated ? clock(p.updated) + " · " + hrs(now - p.updated) + " ago" : "–"}</td>
        <td class="whytxt">${p.newer_inputs.length ? p.newer_inputs.length + ": " + p.newer_inputs.join(", ") : "none"}</td><td class="whytxt">${p.processing || ""}</td></tr>`)}</tbody></table></div>
    </div>
  </div>`;
}

function Capacity({ cap, now }) {
  if (!cap || !cap.jobs) return html`<p class="empty">Reading the runner's records…</p>`;
  const target = 5000;
  const big = cap.biggest || { job: "–", peak_mb: 0 };
  const fits = big.peak_mb <= target;
  const hoursSince = cap.samples_from ? (now - cap.samples_from) / 3600 : 0;
  const X0 = 40, X1 = 1160, Y0 = 20, Y1 = 190, bw = (X1 - X0) / 24;
  const maxWait = Math.max(5, ...cap.by_hour.map((h) => h.waiting || 0));
  const total = cap.jobs.reduce((a, j) => a + j.cpu_h, 0) || 1;
  return html`<div class="grid">
    ${hoursSince < 23 ? html`<p class="note s12" style="margin:0">The records reach back ${hoursSince.toFixed(0)} h; the waits are counted from 21.10.2026 evening on (when the runner began recording when a job became due).</p>` : null}
    <div class="panel s3"><div class="verdict"><h2>CPU</h2><div class="v">${cap.cpu_day != null ? cap.cpu_day + "%" : "–"} <small style="font:500 14px var(--body);color:var(--muted)">of the day</small></div>
      <div class="d">All ${cap.threads} threads together; peak ${cap.cpu_peak != null ? cap.cpu_peak + "%" : "–"} in a minute.</div></div></div>
    <div class="panel s3"><div class="verdict"><h2>Memory</h2><div class="v" style=${cap.mem_full_h > 1 ? "color:var(--warn)" : ""}>${cap.mem_full_h} h full</div>
      <div class="d">Minutes with under 1.5 GB free, added up.</div></div></div>
    <div class="panel s3"><div class="verdict"><h2>Waiting</h2><div class="v" style=${cap.waited_h > 2 ? "color:var(--warn)" : ""}>${cap.waited_h} h</div>
      <div class="d">Due jobs' waits added up; ${cap.long_waits} over 15 min.</div></div></div>
    <div class="panel s3"><div class="verdict"><h2>Mac mini (16 GB)</h2><div class="v" style=${fits ? "color:var(--ok)" : "color:var(--bad)"}>${fits ? "Fits" : "Not yet"}</div>
      <div class="d">Jobs used ${cap.cpu_h_day} CPU hours (the Mac has about 150-170 a day). Biggest: ${big.job} ${(big.peak_mb / 1000).toFixed(1)} GB, target 5 beside WRF.</div></div></div>
    <div class="panel s12"><h2>The machine through the day · CPU by who, jobs waiting · Riga time</h2>
      <svg class="chart" viewBox="0 0 1200 230" role="img" aria-label="CPU use by hour and jobs waiting">
        ${[0, 50, 100].map((v) => { const y = Y1 - (Y1 - Y0) * v / 100; return html`<line x1=${X0} x2=${X1} y1=${y} y2=${y} stroke="var(--line)"/><text x=${X0 - 6} y=${y + 4} text-anchor="end">${v}%</text>`; })}
        ${cap.by_hour.map((h) => {
          const x = X0 + h.h * bw + 3, w = bw - 6;
          const hw = (Y1 - Y0) * Math.min((h.wrf || 0) / 100, 1), hj = (Y1 - Y0) * Math.min((h.jobs || 0) / 100, 1);
          return html`${h.free_min != null && h.free_min < 1500 ? html`<rect x=${x} y=${Y1 + 6} width=${w} height="5" rx="2" fill="var(--bad)"/>` : null}
            <rect x=${x} y=${Y1 - hw} width=${w} height=${hw} fill="var(--wrf)"/>
            <rect x=${x} y=${Y1 - hw - hj} width=${w} height=${hj} rx="2" fill="var(--accent)"><title>${pad(h.h)}:00 WRF ${h.wrf ?? "–"}% jobs ${h.jobs ?? "–"}%, ${h.waiting} job-hours waiting</title></rect>
            ${h.h % 3 === 0 ? html`<text x=${x + w / 2} y=${Y1 + 26} text-anchor="middle">${pad(h.h)}:00</text>` : null}`;
        })}
        <path d=${cap.by_hour.map((h, i) => `${i ? "L" : "M"}${(X0 + h.h * bw + bw / 2).toFixed(1)} ${(Y1 - (Y1 - Y0) * (h.waiting || 0) / maxWait).toFixed(1)}`).join(" ")} fill="none" stroke="var(--warn)" stroke-width="2"/>
        <text x=${X1 + 8} y=${Y0 + 4}>${maxWait.toFixed(0)}</text><text x=${X1 + 8} y=${Y1 + 4}>0</text>
      </svg>
      <div class="legend"><span style="--c:var(--wrf)">WRF</span><span style="--c:var(--accent)">jobs</span><span style="--c:var(--warn)">job-hours waiting (right scale)</span><span style="--c:var(--bad)">memory full that hour</span></div></div>
    <div class="panel s12"><h2>Each job, last 24 hours · sorted by CPU</h2>
      <div class="tablewrap"><table style="min-width:900px"><thead><tr><th>Job</th><th class="r">Runs</th><th class="r">CPU hours</th><th>Share of the CPU</th><th class="r">Time running</th><th class="r">Peak memory</th><th class="r">Downloaded</th><th class="r">Waited</th><th class="r">Longest wait</th><th class="r">Failed</th></tr></thead>
      <tbody>${cap.jobs.map((j) => html`<tr><td><b>${j.job}</b></td><td class="r num">${j.runs}</td><td class="r num">${j.cpu_h.toFixed(1)}</td>
        <td><div class="hbar"><i style=${`width:${Math.min(j.cpu_h / total * 250, 100).toFixed(0)}%`}></i></div></td>
        <td class="r num">${j.wall_h.toFixed(1)} h</td><td class="r num">${(j.peak_mb / 1000).toFixed(1)} GB</td><td class="r num">${j.net_gb.toFixed(1)} GB</td>
        <td class="r num" style=${j.waited_h >= 1 ? "color:var(--warn)" : ""}>${j.waited_h.toFixed(1)} h</td><td class="r num">${hrs(j.longest_wait)}</td>
        <td class="r num" style=${j.failed ? "color:var(--bad)" : ""}>${j.failed || ""}</td></tr>`)}</tbody></table></div>
      <p class="note">CPU hours: thread-hours used (one thread busy for an hour is 1). Waited: from the minute a job was due to its start; this is where the machine runs short. Shadow runs make whole sets, so they show more work than the live jobs will.</p></div>
  </div>`;
}

// admin.meteogust.com's overview, read-only (its /api/overview through the service token on the Tower).
const ago = (t, now) => t ? hrs(now - t) + " ago" : "–";
function KV({ rows }) {
  return html`<div class="kv">${rows.filter(Boolean).map(([k, v, style]) => html`<span>${k}</span><span style=${style || ""}>${v}</span>`)}</div>`;
}

function AdminCards({ o, now }) {
  const cards = [];
  // The helper's hourly job on GitHub (update.yml): its last 24 hours.
  const runs = (o.runs || []).filter((r) => r.started >= now - 86400);
  // Its results are "published", "nothing new" or "failed" (and the steps that failed in "failed").
  const failed = runs.filter((r) => r.result === "failed" || (r.failed || []).length);
  const published = runs.filter((r) => r.result === "published").length;
  const lastFail = failed.sort((a, b) => b.started - a.started)[0];
  const h = o.helper || {};
  cards.push(html`<div class="panel"><h2>Helper's hourly job · 24 h</h2><${KV} rows=${[
    ["Runs", `${runs.length} · ${published} published, ${runs.length - published - failed.length} nothing new`], ["Failed", failed.length, failed.length ? "color:var(--bad)" : ""],
    lastFail ? ["Last failure", `${clock(lastFail.started)}${(lastFail.failed || []).length ? " · " + lastFail.failed.slice(0, 3).join(", ") : ""}`] : null,
    h.running ? ["Running now", `${h.running.step || "…"} · since ${clock(h.running.started)}`] : ["Last finished", ago(h.lastEnd || h.lastStart, now)],
    h.queued ? ["Queued", h.queued] : null]} /></div>`);
  // Stations.
  const st = o.stations || {};
  cards.push(html`<div class="panel"><h2>Stations</h2><${KV} rows=${[
    ["Fresh in 20 min", `${st.fresh ?? "–"} of ${st.total ?? "–"}`, st.total && st.fresh < st.total / 2 ? "color:var(--bad)" : ""],
    ["Newest reading", ago(st.newest, now)],
    ...(st.stale || []).slice(0, 5).map((x) => [x.station, "silent " + ago(x.time, now)])]} />
    <a class="btn" href="https://admin.meteogust.com/#stations">Stations in admin ↗</a></div>`);
  // Ours against Open-Meteo on the same run (the first 48 hours): the biggest differences.
  const ours = ((o.ours || {}).rows || []).filter((r) => r.lead === "0-48" && r.n >= 50).sort((a, b) => b.mae - a.mae);
  cards.push(html`<div class="panel"><h2>Our data vs Open-Meteo · same run, 0-48 h</h2><${KV} rows=${[
    ...ours.slice(0, 6).map((r) => [r.model.replace("#temp", " air"), `${r.mae.toFixed(2)} ${r.model.endsWith("#temp") ? "°C" : "m/s"} · ${r.n.toLocaleString("en")} h`,
      r.mae > (r.model.endsWith("#temp") ? 0.5 : 0.3) ? "color:var(--warn)" : ""]),
    ours.length ? null : ["Compared", "no rows yet"]]} /></div>`);
  // Open-Meteo calls (admins' "vs OM" and the server's comparisons): today.
  const om = o.openMeteo || [];
  const today = Math.max(0, ...om.map((x) => x.day));
  const todays = om.filter((x) => x.day === today);
  const sum = (k) => todays.reduce((a, x) => a + (x[k] || 0), 0);
  const refused = todays.filter((x) => x.kind.includes("refused")).reduce((a, x) => a + x.hits, 0);
  cards.push(html`<div class="panel"><h2>Open-Meteo relay · today</h2><${KV} rows=${[
    ["Requests", sum("hits").toLocaleString("en")], ["Fetched from Open-Meteo", sum("fetches").toLocaleString("en")],
    ["Weight (counts toward the cap)", sum("weight").toLocaleString("en")], refused ? ["Refused", refused, "color:var(--warn)"] : null]} /></div>`);
  // Crashes, 30 days.
  const cr = o.crashes || {};
  const groups = cr.groups || [];
  const newest = groups.slice().sort((a, b) => b.last - a.last)[0];
  cards.push(html`<div class="panel"><h2>Crashes · 30 days</h2><${KV} rows=${[
    ["iPhone", groups.filter((g) => g.platform === "ios").reduce((a, g) => a + g.n, 0)],
    ["Android", groups.filter((g) => g.platform !== "ios").reduce((a, g) => a + g.n, 0)],
    ["Kinds", groups.length],
    newest ? ["Newest", `${ago(newest.last, now)} · ${(newest.summary || newest.kind || "").slice(0, 40)}`] : null]} /></div>`);
  // Rain and wind alerts.
  const al = o.alerts || {};
  const sent = al.sent || [];
  cards.push(html`<div class="panel"><h2>Rain and wind alerts</h2><${KV} rows=${[
    ["Rain alerts", `${(al.rain || {}).phones ?? 0} phones · ${(al.rain || {}).places ?? 0} places`],
    ["Wind alerts", `${(al.wind || {}).phones ?? 0} phones · ${(al.wind || {}).spots ?? 0} spots`],
    ...sent.map((x) => [`Sent today (${x.kind})`, `${x.ok}${x.failed ? " · " + x.failed + " failed" : ""}`, x.failed ? "color:var(--warn)" : ""]),
    al.last ? ["Last alert", `${ago(al.last.time, now)} · ${al.last.kind} · ${al.last.place}`] : null,
    (al.errors || []).length ? ["Errors (recent)", `${al.errors.length} · last: ${al.errors[0].reason}`.slice(0, 60), "color:var(--warn)"] : null]} /></div>`);
  // Builds.
  const b = o.builds || {};
  const nb = (b.builds || [])[0], nr = (b.runs || [])[0];
  cards.push(html`<div class="panel"><h2>Builds</h2><${KV} rows=${[
    nb ? ["Newest build", `${nb.number} · ${nb.result || nb.progress || ""}`] : null,
    nr ? ["Last workflow", `${nr.title.slice(0, 32)} · ${nr.conclusion || nr.status}`] : null,
    nr ? ["Started", ago(nr.created, now)] : null]} />
    <a class="btn" href="https://admin.meteogust.com/">Builds in admin ↗</a></div>`);
  // Pushes, the last day.
  const pushes = o.pushes || [];
  const pday = Math.max(0, ...pushes.map((x) => x.day));
  const pt = pushes.filter((x) => x.day === pday);
  if (pt.length) cards.push(html`<div class="panel"><h2>Pushes · last day</h2><${KV} rows=${pt.map((x) => [x.kind, `${x.ok} sent${x.failed ? " · " + x.failed + " failed" : ""}`, x.failed ? "color:var(--warn)" : ""])} /></div>`);
  // The station collector's own state.
  cards.push(html`<div class="panel"><h2>Collector</h2><${KV} rows=${(o.collector || []).map((c) => {
    const v = c.value, k = c.key.replace(/_/g, " ").replace(/^windguru/, "source");
    const asTime = typeof v === "number" && v > 1.5e9 && v < 2.5e9;
    return [k, asTime ? ago(v, now) : String(v)];
  })} /></div>`);
  return cards;
}

function Admin({ ad, now }) {
  if (!ad || !ad.feeds) return html`<p class="empty">Reading the feeds…</p>`;
  const late = ad.feeds.filter((f) => f.late).length;
  return html`<p class="note" style="margin:0 0 12px">admin.meteogust.com's data, read-only, every minute while open. Changes (approve a spot, add a station, start a build) stay on <a href="https://admin.meteogust.com/" style="color:var(--accent)">admin.meteogust.com</a>, which also keeps working when home is down.</p>
    <div class="cards">
      <div class="panel"><h2>Feeds and alerts · ${late ? late + " late" : "all fine"}</h2>
        ${ad.feeds.map((f) => html`<div class="feed"><span>${f.name}<span class="sub">${f.usual}</span>${f.frames ? html`<span class="sub">${satNote(f.frames)}</span>` : ""}${f.score ? html`<span class="sub">${scoreNote(f.score)}</span>` : ""}</span><span class="num">${f.age != null ? hrs(f.age) + " ago" : "not found"}</span>
          <span class=${"st " + (f.late ? "bad" : "ok")}>${f.late ? "late" : "fine"}</span></div>`)}
        <a class="btn" href="https://admin.meteogust.com/">Alert settings in admin ↗</a></div>
      ${ad.error === "token" ? html`<div class="panel"><h2>The rest of admin's overview</h2>
          <p class="note">Needs the admin service token on the Tower: add <b>SHOREWIND_ADMIN_ID</b> and <b>SHOREWIND_ADMIN_SECRET</b> to /root/.meteogust.env.</p></div>`
        : ad.error ? html`<div class="panel"><h2>Admin's overview</h2><p class="note">${ad.error}</p></div>`
        : ad.overview ? html`<${AdminCards} o=${ad.overview} now=${now} />` : null}
    </div>`;
}

function NowTab({ f, s, now }) {
  return html`<div class="grid">
      <${Machine} f=${f} />
      <${Running} f=${f} now=${now} />
      <${Next} f=${f} />
      <${Timeline} s=${s} now=${now} />
      <${Sources} f=${f} s=${s} />
      <${Runs} s=${s} />
    </div>`;
}

// MARK: The control panel (the owner, 21.10.2026: admin and ops in one, at home; mockups control-pipeline and
// control-sections in docs/mockups). Admin's data and actions come through the home server's /admin/api/.

const SECTIONS = [["pipeline", "Pipeline"], ["machine", "Machine"], ["quality", "Quality"], ["rain", "Rain & clouds"], ["app", "App & riders"], ["stations", "Stations"], ["costs", "Costs"]];
const JOB_SET = { "icon-eu": "icon-eu", "world-gfs": "gfs", "world-ecmwf": "ecmwf", "world-icon": "icon", "world-gdps": "gdps", "nordic-1km": "metnordic-1km",
  "mix": "mix", "mix-world": "mix-world", "weather-mix-europe": "mix-wx", "weather-mix-world": "mix-world-wx", "bucket-knmi-eu": "knmi-eu",
  "bucket-arome25": "arome25", "bucket-icon-ch2": "icon-ch2", "ukmo": "ukmo", "ewam": "ewam", "ecmwf-waves": "ecmwf-waves", "chance": "chance",
  "harm-fi": "harm-fi", "aladin": "aladin", "regional-wind": "metnordic", "regional-weather": "metnordic-wx", "sea": "sea", "waves": "waves",
  "water": "water" };

// Each map's home jobs (regional_tiles.py's wind job makes the blend and each regional model's own set).
const SET_JOBS = { metnordic: ["regional-wind"], "metnordic-wx": ["regional-weather"], dini: ["regional-dini"], arome: ["regional-arome"],
  "icon-d2": ["regional-icon-d2"], "harm-nl": ["regional-harm-nl"], "icon-ch1": ["regional-icon-ch1"], "icon-2i": ["regional-icon-2i"],
  "icon-eu": ["icon-eu"], "icon-eu-wx": ["icon-eu"], "icon-eu-steer": ["icon-eu"], ecmwf: ["world-ecmwf"], gfs: ["world-gfs"],
  icon: ["world-icon"], gdps: ["world-gdps"], ewam: ["ewam"], "ecmwf-waves": ["ecmwf-waves"], "metnordic-1km": ["nordic-1km"],
  "harm-fi": ["harm-fi"], aladin: ["aladin"], mix: ["mix"], "mix-world": ["mix-world"], "mix-wx": ["weather-mix-europe"],
  "mix-world-wx": ["weather-mix-world"], ukmo: ["ukmo"], "icon-ch2": ["bucket-icon-ch2"], arome25: ["bucket-arome25"],
  "knmi-eu": ["bucket-knmi-eu"], chance: ["chance"], sea: ["sea"], waves: ["waves"], water: ["water"] };

async function adminGet(path) {
  const r = await fetch("/admin/api/" + path, { cache: "no-store" });
  if (!r.ok) throw new Error((await r.text()).slice(0, 200));
  return r.json();
}
async function adminPost(path, body) {
  const r = await fetch("/admin/api/" + path, { method: "POST", headers: { "content-type": "application/json", "x-shorewind-admin": "1" }, body: JSON.stringify(body) });
  const text = await r.text();
  let data = {};
  try { data = JSON.parse(text); } catch (e) { data = { error: text.slice(0, 200) }; }
  if (!r.ok || data.error) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

// A button that asks once more before it acts; shows what came back.
function ActButton({ label, ask, run, primary, danger }) {
  const [state, setState] = useState("idle");
  const [msg, setMsg] = useState("");
  const go = async () => {
    if (state === "idle") { setState("ask"); return; }
    setState("busy");
    try { const d = await run(); setMsg(d && d.ok === false ? "not done" : "done"); setState("done"); }
    catch (e) { setMsg(String(e.message || e)); setState("error"); }
  };
  return html`<span style="display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap">
    <button class=${"btn" + (primary ? " primary" : "") + (danger ? " danger" : "")} onClick=${go} disabled=${state === "busy"}>
      ${state === "ask" ? (ask || "Sure? Click again") : state === "busy" ? "…" : label}</button>
    ${state === "ask" ? html`<button class="btn" onClick=${() => setState("idle")}>Cancel</button>` : null}
    ${msg ? html`<span class="whytxt" style=${state === "error" ? "color:var(--bad)" : ""}>${msg}</span>` : null}</span>`;
}

function Nav({ sec }) {
  return html`<nav class="tabs" aria-label="Sections">
    ${SECTIONS.map(([k, label]) => html`<a href=${"#" + k} class=${sec === k ? "on" : ""}>${label}</a>`)}
    <a href="/admin/" title="admin.meteogust.com's own page, served from home">Full admin ↗</a></nav>`;
}

// The satellite clouds' last day (views.SatFrames, 28.10.2026): its quarter hours missing (held by clouds.py's guard, no
// picture, a run lost) and its frames off every neighbour (made from broken pictures).
const satNote = (s) => `${s.frames} of 96 frames` + (s.missing.length ? ` · missing ${s.missing.slice(-4).map((t) => clock(t)).join(", ")}${s.missing.length > 4 ? " and " + (s.missing.length - 4) + " more" : ""}` : "")
  + (s.broken.length ? ` · broken ${s.broken.map((t) => clock(t)).join(", ")}` : "");
// Their scorecard against 20 airports' METARs (clouds_score.py, 01.11.2026): yesterday's (else today's) share of overcast
// reports drawn clear and of clear ones drawn cloud, by the sun: night (under -6), low sun (-6 to 15), day.
const scoreNote = (s) => {
  const days = Object.keys(s.days || {}).sort();
  const day = days.length > 1 ? days[days.length - 2] : days[0];
  if (!day) return "";
  const share = (bad, n) => (n ? Math.round((100 * bad) / n) + "%" : "–");
  const parts = [["night", "night"], ["low", "low sun"], ["day", "day"]].filter(([k]) => s.days[day][k])
    .map(([k, name]) => { const c = s.days[day][k]; return `${name} ${share(c[1], c[0])} of ${c[0]} / ${share(c[3], c[2])} of ${c[2]}`; });
  return `vs airports ${day.slice(8)}.${day.slice(5, 7)}: overcast drawn clear / clear drawn cloud: ${parts.join(" · ")}`;
};

function warningsList({ f, fr, ad, spots, now }) {
  const out = [];
  const sat = ((ad && ad.feeds) || []).find((x) => x.key === "sat" && x.frames);
  if (sat && sat.frames.broken.length) out.push(["bad", "Satellite clouds", "broken frame " + sat.frames.broken.map((t) => clock(t)).join(", "), "#pipeline"]);
  else if (sat && sat.frames.missing.some((t) => now - t < 6 * 3600))
    out.push(["warn", "Satellite clouds", sat.frames.missing.filter((t) => now - t < 6 * 3600).map((t) => clock(t)).join(", ") + " missing", "#pipeline"]);
  const scored = ((ad && ad.feeds) || []).find((x) => x.key === "sat" && x.score);
  for (const g of (scored && scored.score.flags) || [])
    out.push(["warn", "Satellite clouds vs airports", `${g.day.slice(8)}.${g.day.slice(5, 7)} ${g.band === "low" ? "low sun" : g.band}: ${g.what} ${Math.round(100 * g.share)}% (usual ${Math.round(100 * g.usual)}%)`, "#rain"]);
  for (const r of (fr && fr.rows) || []) if (r.status === "bad") out.push(["bad", r.label, `${r.behind_runs} run${r.behind_runs > 1 ? "s" : ""} behind${r.waited ? " · " + hrs(r.waited) : ""}`, "#pipeline"]);
  for (const x of (ad && ad.feeds) || []) if (x.late) out.push(["bad", x.name, x.age != null ? "late · " + hrs(x.age) : "not found", "#pipeline"]);
  const o = ad && ad.overview;
  if (o) {
    const over = ((o.ours || {}).rows || []).filter((r) => r.lead === "0-48" && r.n >= 100 && r.mae > (r.model.endsWith("#temp") ? 0.5 : 0.3));
    if (over.length) out.push(["warn", "Ours vs Open-Meteo", `${over.length} over the limit`, "#quality"]);
    const crash = ((o.crashes || {}).groups || []).filter((g) => now - g.last < 3 * 3600).sort((a, b) => b.last - a.last)[0];
    if (crash) out.push(["warn", "Crash", `${crash.kind || crash.summary || ""} · ${crash.platform} · ${hrs(now - crash.last)} ago`, "#app"]);
    const failed = ((o.alerts || {}).sent || []).reduce((a, s) => a + (s.failed || 0), 0);
    if (failed) out.push(["warn", "Alerts", `${failed} push${failed > 1 ? "es" : ""} failed in 24 h`, "#app"]);
    // Admin alerts with no admin phone to go to (no push failed; the owner, 29.10.2026: "59 pushes failed today" were these).
    const nobody = (o.alerts || {}).noAdminPhone;
    if (nobody) out.push(["warn", "Alerts", `no admin phone registered · last ${hrs(now - nobody.last)} ago`, "#app"]);
    const st = o.stations || {};
    if (st.total && st.fresh < st.total * 0.8) out.push(["warn", "Stations", `${st.fresh} of ${st.total} fresh`, "#stations"]);
  }
  if (spots && (spots.pending || []).length) out.push(["warn", "Spots", `${spots.pending.length} waiting for review`, "#app"]);
  if (f && f.live_t && now - f.live_t > 180) out.push(["bad", "Runner", `quiet for ${dur(now - f.live_t)}`, "#machine"]);
  if (f && f.beat && now - f.beat > 1200) out.push(["bad", "Heartbeat", `${dur(now - f.beat)} old`, "#machine"]);
  return out;
}

function Warnings(props) {
  const { f, ad, now } = props;
  const list = warningsList(props);
  const fine = ((ad && ad.feeds) || []).filter((x) => !x.late).length;
  const summaryFull = `${fine} feeds fine · ${(f.live || []).length} live at home · ${f.mac && f.mac.wrf ? (f.mac.wrf.running ? "WRF on the Mac" + (f.mac.wrf.progress != null ? " " + Math.round(f.mac.wrf.progress * 100) + "%" : "") : "WRF on the Mac: " + (f.mac.wrf.stage === "waiting" ? "waiting for GFS" : f.mac.wrf.stage)) : f.wrf && f.wrf.running ? "WRF running" : f.wrf && f.wrf.waiting ? "WRF waiting for GFS" : (f.mac ? "WRF on the Mac next " : "WRF next ") + clock((f.wrf && f.wrf.next || [])[0])} · heartbeat ${f.beat ? dur(now - f.beat) : "–"}${f.mac ? ` · Mac heartbeat ${dur(now - f.mac.beat)}` : ""}`;
  // The chip's own words short enough for one row at ~1670 px (the owner, 25.10.2026); the whole sentence on hover.
  const summary = `${fine} feeds fine · ${(f.live || []).length} live · ${f.mac && f.mac.wrf ? (f.mac.wrf.running ? "WRF on the Mac" + (f.mac.wrf.progress != null ? " " + Math.round(f.mac.wrf.progress * 100) + "%" : "") : "WRF on the Mac: " + (f.mac.wrf.stage === "waiting" ? "waiting for GFS" : f.mac.wrf.stage)) : f.wrf && f.wrf.running ? "WRF running" : f.wrf && f.wrf.waiting ? "WRF waiting for GFS" : (f.mac ? "WRF on the Mac next " : "WRF next ") + clock((f.wrf && f.wrf.next || [])[0])} · heartbeats ${f.beat ? dur(now - f.beat) : "–"}${f.mac ? `, Mac ${dur(now - f.mac.beat)}` : ""}`;
  return html`<div class="warnings">
    ${list.map(([k, a, b, href]) => html`<a class=${"w " + k} href=${href} title=${a + " " + b}><b>${a}</b> ${b}</a>`)}
    <a class="w ok sum" href="#machine" title=${summaryFull}>${summary}</a>
  </div>`;
}

// Auto (the system's), Light or Dark, remembered in this browser; the head's script applies it before the first paint.
function ThemeSwitch() {
  const [theme, setThemeState] = useState(() => document.documentElement.dataset.theme || "auto");
  const setTheme = (t) => {
    if (t === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
    try { if (t === "auto") localStorage.removeItem("ops-theme"); else localStorage.setItem("ops-theme", t); } catch (e) {}
    setThemeState(t);
  };
  return html`<span class="theme" role="group" aria-label="Theme">${[["auto", "Auto"], ["light", "Light"], ["dark", "Dark"]].map(([k, label]) =>
    html`<button class=${theme === k ? "on" : ""} aria-pressed=${theme === k} title=${k === "auto" ? "Follow the system's light or dark" : label + " theme"}
      onClick=${() => setTheme(k)}>${label}</button>`)}</span>`;
}

function TopBar(props) {
  const { f, zone, setZone, sec } = props;
  const now = Date.now() / 1000;
  const stale = f.live_t && now - f.live_t > 180;
  return html`<header class="top"><div class=${"wrap" + (sec === "pipeline" ? " wide" : "")}><div class="row1">
    <div class="brand"><span class="dot" aria-hidden="true" style=${stale ? "background:var(--bad)" : ""}></span>MeteoGust <small>control · ${f.host || "…"}${f.mac ? html` + <span style="color:var(--mac)"><span aria-hidden="true" style=${now - f.mac.beat > 600 ? "color:var(--bad)" : ""}>● </span>Mac mini</span>` : null}</small></div>
    <${Nav} sec=${sec} />
    <div class="clock"><b>${clock(now, true)}</b> ${zoneName()}
      <button class="tbtn" onClick=${() => setZone(zone === "UTC" ? "Europe/Riga" : "UTC")}>show ${zone === "UTC" ? "Riga" : "UTC"}</button>
      <${ThemeSwitch} /></div>
  </div><${Warnings} ...${props} now=${now} /></div></header>`;
}

