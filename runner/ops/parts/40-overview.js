// MARK: App & riders

function AppRiders({ ad, spots, roles, builds, refresh, now, goTab }) {
  const o = ad && ad.overview;
  const [notes, setNotes] = useState("");
  const [num, setNum] = useState("");
  const newest = builds && builds.builds && builds.builds[0];
  const accounts = (roles && roles.accounts) || [];
  const devices = (roles && roles.devices) || [];
  const active = devices.filter((d) => now - d.last_seen < 7 * 86400).length;
  const groups = (o && o.crashes && o.crashes.groups) || [];
  return html`<div class="grid">
    <div class="panel s4"><h2>Spots waiting for review · ${spots ? (spots.pending || []).length : "…"}</h2>
      ${spots && (spots.pending || []).length ? spots.pending.map((p) => html`<div class="job" style="flex-direction:column;align-items:stretch;margin-bottom:8px">
        <div><b>${p.name}</b> <span class="whytxt">by ${p.author || "a rider"} · ${hrs(now - (p.created || now))} ago</span></div>
        <span class="whytxt">${(+p.lat).toFixed(3)}, ${(+p.lon).toFixed(3)}</span>
        <div class="btns"><${ActButton} label="Approve" primary=${true} run=${async () => { const d = await adminPost("spots", { code: p.code, action: "approve" }); refresh(); return d; }} />
          <button class="btn" onClick=${() => goTab("spots")}>Details</button>
          <${ActButton} label="Reject" danger=${true} run=${async () => { const d = await adminPost("spots", { code: p.code, action: "reject" }); refresh(); return d; }} /></div></div>`)
        : html`<p class="whytxt">Nothing waiting.</p>`}
      <p class="whytxt">Public rider spots ${spots ? (spots.published || []).length : "…"} · catalog ${spots ? (spots.official || []).length : "…"} · <a href="#app" onClick=${(e) => { e.preventDefault(); goTab("spots"); }} style="color:var(--accent)">all spots, suggestions, the editor</a></p></div>
    <div class="panel s4"><h2>Builds and releases</h2>
      <div class="kv">${newest ? html`<span>Newest build</span><span>${newest.number} · ${newest.result || newest.progress || ""}</span><span>Started</span><span>${newest.started ? hrs(now - newest.started) + " ago" : "–"}</span>` : html`<span>Builds</span><span>…</span>`}</div>
      <div class="btns" style="margin-top:12px"><${ActButton} label="Start build" primary=${true} run=${() => adminPost("run", { what: "build" })} /></div>
      <div class="confirm" style="margin-top:12px"><b>Release to Kite friends</b> (external TestFlight). Type the build number and the notes, then confirm twice.
        <div class="form" style="margin-top:8px"><label>Build<input value=${num} onInput=${(e) => setNum(e.target.value)} placeholder=${newest ? String(newest.number) : ""}/></label>
          <label>Notes<input value=${notes} onInput=${(e) => setNotes(e.target.value)} placeholder="What's in it"/></label></div>
        <div style="margin-top:8px">${num && notes.length > 3 ? html`<${ActButton} label=${"Release build " + num} ask=${"Release " + num + " to Kite friends? Click again"} danger=${true} run=${() => adminPost("run", { what: "release", number: num, notes })} />` : html`<span class="whytxt">Fill both to release.</span>`}</div></div></div>
    <div class="panel s4"><h2>Crashes · 30 days</h2><div class="tablewrap"><table><tbody>
      ${groups.slice().sort((a, b) => b.last - a.last).slice(0, 8).map((g) => html`<tr><td><span class=${"st " + (g.kind === "crash" || g.kind === "anr" ? "bad" : "wait")}>${g.kind || "crash"}</span></td>
        <td>${g.platform} ${g.builds || ""} · ${g.n}× · ${(g.summary || "").slice(0, 50)}</td><td class="r num">${hrs(now - g.last)}</td></tr>`)}</tbody></table></div></div>
    <div class="panel s6"><h2>Riders</h2><div class="kv">
      <span>Accounts</span><span>${accounts.length}</span><span>Devices</span><span>${devices.length}</span><span>Active 7 days</span><span>${active}</span>
      <span>Admins</span><span>${accounts.filter((a) => a.role === "admin").length}</span>
      <span>Rain alerts</span><span>${o ? `${(o.alerts.rain || {}).phones ?? 0} phones · ${(o.alerts.rain || {}).places ?? 0} places` : "…"}</span>
      <span>Wind alerts</span><span>${o ? `${(o.alerts.wind || {}).phones ?? 0} phones` : "…"}</span></div>
      <div class="tablewrap" style="margin-top:10px"><table><thead><tr><th>Rider</th><th>Role</th><th>Signed in with</th><th class="r">Last seen</th></tr></thead><tbody>
        ${accounts.slice().sort((a, b) => b.last_seen - a.last_seen).map((a) => html`<tr><td>${a.nickname || [a.first, a.last].filter(Boolean).join(" ") || "–"}</td><td>${a.role}</td>
          <td class="whytxt">${[a.apple ? "Apple" : null, a.google ? "Google" : null].filter(Boolean).join(", ") || "email"}</td><td class="r num">${a.last_seen ? hrs(now - a.last_seen) + " ago" : "–"}</td></tr>`)}</tbody></table></div>
      <p class="whytxt">Every rider with builds, phones, days active and charts: <a href="#" onClick=${(e) => { e.preventDefault(); goTab("roles"); }} style="color:var(--accent)">Riders &amp; roles</a>.</p></div>
    <div class="panel s6"><h2>Features</h2><div class="tablewrap"><table><tbody>
      ${((roles && roles.features) || []).map((x) => html`<tr><td>${x.name}</td><td class="whytxt">${x.grp || ""}</td><td class="whytxt">${(x.roles || []).join(", ") || "nobody"}</td><td class="whytxt">${x.release || ""}</td></tr>`)}</tbody></table></div>
      <p class="whytxt">Change who sees a feature: <a href="/admin/#features" style="color:var(--accent)">full admin, Features</a>.</p></div>
  </div>`;
}

// MARK: Stations

function StationsSec({ ad, st, now }) {
  const o = ad && ad.overview;
  const s = (o && o.stations) || {};
  const list = (st && st.stations) || [];
  const silent = list.filter((x) => x.time && now - x.time > 1200 && !x.hidden).sort((a, b) => a.time - b.time);
  const heat = (o && o.heat) || {};
  return html`<div class="grid">
    <div class="panel s3"><h2>Fresh in 20 min</h2><div class="big">${s.fresh ?? "–"} <small>of ${s.total ?? "–"}</small></div></div>
    <div class="panel s3"><h2>Silent over 20 min</h2><div class="big" style=${silent.length ? "color:var(--warn)" : ""}>${silent.length}</div></div>
    <div class="panel s3"><h2>Newest reading</h2><div class="big">${s.newest ? hrs(now - s.newest) : "–"} <small>ago</small></div></div>
    <div class="panel s3"><h2>Stations listed</h2><div class="big">${list.length}</div><p class="whytxt">${((st && st.lists) || []).map((l) => `${l.source} ${l.n}`).join(" · ")}</p></div>
    <div class="panel s6"><h2>Silent stations</h2><div class="tablewrap"><table><thead><tr><th>Station</th><th>Source</th><th class="r">Last reading</th></tr></thead><tbody>
      ${silent.slice(0, 15).map((x) => html`<tr><td>${x.name}</td><td class="whytxt">${x.source}</td><td class="r num" style="color:var(--warn)">${hrs(now - x.time)} ago</td></tr>`)}</tbody></table></div></div>
    <div class="panel s6"><h2>Readings per hour · worst first · 24 h</h2>${heat.stations ? html`<div class="heat">${(heat.stations || []).map(([name, counts]) => [name, counts || [], (counts || []).filter((c) => !c).length])
        .sort((a, b) => b[2] - a[2]).slice(0, 12).map(([name, counts]) => {
        const usual = Math.max(1, ...counts);
        return html`<span title=${name}>${name}</span>${counts.slice(-24).map((c) => html`<i style=${`background:${!c ? "var(--bad)" : c < usual * 0.6 ? "var(--home-soft)" : "var(--home)"}`}></i>`)}`;
      })}</div>` : html`<p class="whytxt">…</p>`}</div>
    <div class="panel s12"><h2>All stations</h2><div class="tablewrap"><table><thead><tr><th>Station</th><th>Source</th><th>Code</th><th class="r">Last reading</th><th></th></tr></thead><tbody>
      ${list.slice().sort((a, b) => (a.time || 0) - (b.time || 0)).map((x) => html`<tr><td>${x.name}${x.hidden ? html` <span class="whytxt">(hidden)</span>` : null}</td><td class="whytxt">${x.source}</td><td class="num whytxt">${x.code}</td>
        <td class="r num" style=${x.time && now - x.time > 1200 ? "color:var(--warn)" : ""}>${x.time ? hrs(now - x.time) + " ago" : "–"}</td><td class="whytxt">${x.note || ""}</td></tr>`)}</tbody></table></div>
      <p class="whytxt">Add or edit a station: <a href="/admin/#stations" style="color:var(--accent)">full admin, Stations</a>.</p></div>
  </div>`;
}

// MARK: Costs

// Spending by day and the billing month (admin's overview.js spending; the owner, 12.11.2026: "costs summary for daily
// spending and ... cap limit"): the month's bill so far and at this pace, each item; the last 30 days' spending past the
// free shares as bars over the billing month (the owner, 17.11.2026: "Same for the spending chart, start from 7th"), the
// warning ($1: a push) and the hold ($3: a runaway folder's uploads wait) as lines; no pace line: the days' dollars past
// their shares don't add up to the bill (writes bill per started million), the month card has that.
function spendPanels(sp, storeCost) {
  if (!sp || !sp.month) return html`<div class="panel s3"><h2>This month, estimated</h2><div class="big">$${(5 + storeCost).toFixed(2)}</div><p class="whytxt">Workers Paid $5 + file store $${storeCost.toFixed(2)} (no spending data from admin yet).</p></div>`;
  const usd = (v) => "$" + (v < 10 ? v.toFixed(2) : v.toFixed(0));
  const names = { ...sp.names, store: "File store size", base: "Workers Paid" };
  const items = Object.entries(sp.month.projected.items).filter(([, v]) => v >= 0.005).sort((a, b) => b[1] - a[1]);
  const today = sp.days[sp.days.length - 1];
  const colour = (v) => v > sp.hold ? "var(--bad)" : v > sp.warn ? "var(--warn)" : "var(--accent)";
  return html`
    <div class="panel s3"><h2>This month, at this pace</h2><div class="big">${usd(sp.month.projected.total)}</div>
      <p class="whytxt">${usd(sp.month.soFar.total)} so far, ${sp.month.from.slice(5)} to ${sp.month.to.slice(5)} (Cloudflare's bill, day ${Math.ceil(sp.month.elapsed)} of ${sp.month.length}).</p>
      <div class="kv">${items.map(([k, v]) => html`<span>${names[k] || k}</span><span class="num">${usd(v)}</span>`)}</div></div>
    <div class="panel s3"><h2>Today</h2><div class="big" style=${`color:${today ? colour(today.over) : "inherit"}`}>${today ? usd(today.over) : "–"}</div>
      <p class="whytxt">Past the free shares (a 30th of each month's allowance a day). A push at ${usd(sp.warn)}; at ${usd(sp.hold)} a folder writing over 3× its normal day waits until midnight UTC.</p>
      ${sp.held && sp.held.length ? html`<p class="whytxt" style="color:var(--bad)">Held today: ${sp.held.join(", ")}</p>` : ""}
      ${sp.errors && sp.errors.length ? html`<p class="whytxt">${sp.errors.join("; ")}</p>` : ""}</div>
    <div class="panel s6"><h2>Spending a day past the free shares · ${sp.month.from.slice(5)} to ${sp.month.to.slice(5)} (UTC)</h2>
      <${MonthBars} sp=${sp} slots=${monthSlots(sp, (d) => d.over)} min=${sp.hold * 1.15} colour=${colour} aria="Spending a day past the free shares"
        lines=${[{ v: sp.warn, colour: "var(--warn)", text: `${usd(sp.warn)} push` }, { v: sp.hold, colour: "var(--bad)", text: `${usd(sp.hold)} hold` }]}
        tip=${(s1) => s1.v == null ? [] : [html`<span class="k">past the free shares</span> ${usd(s1.v)}`,
          ...(Object.entries(s1.d.dollars).filter(([, v]) => v >= 0.005).sort((p, q) => q[1] - p[1])
            .map(([k, v]) => html`<span class="k">${names[k] || k}</span> ${usd(v)}${k in s1.d.use ? ` · ${Math.round(s1.d.use[k]).toLocaleString("en")}` : ""}`)),
          ...(s1.v < 0.005 ? ["all within the free shares"] : [])]}/></div>
    <${OpsChart} sp=${sp} k="a" free=${1e6 / 30} price=${4.5} cap=${2e6 / 30} title="File store writes (Class A) a day"/>
    <${OpsChart} sp=${sp} k="b" free=${10e6 / 30} price=${0.36} title="File store reads (Class B) a day"/>
  `;
}

// The file store's operations a UTC day in Cloudflare's billing month, the 7th to the 7th as its dashboard counts (the
// owner, 17.11.2026: "daily operations A with B with each day cap and do we go out or below it each day"; then "start
// counting from 7th", "at bottom ... date number", "33k line blends with columns", "average usage line where we will see
// final usage at current pace", "when hover want to see more details"). free: a 30th of the month's allowance, as
// admin's overview.js SPEND_ITEMS (1M writes, 10M reads), price a million past it; Cloudflare bills started millions.
// cap: the writes' 2M a month, the owner's limit (7.11.2026: "hold under 2M"), $9 past it. The pace: the month so far
// over its elapsed days, as the month card's projection.
function OpsChart({ sp, k, free, price, cap = 0, title }) {
  const m = sp.month, slots = monthSlots(sp, (d) => d.use[k]);
  const used = slots.reduce((a, s1) => a + (s1.v || 0), 0), month = free * 30;
  const pace = used / m.elapsed, end = pace * m.length;
  const bill = (v) => Math.ceil(Math.max(0, v - month) / 1e6) * price;
  const n = (v) => v >= 1e6 ? (v / 1e6).toFixed(2).replace(/\.?0+$/, "") + "M" : v >= 1e3 ? Math.round(v / 1e3) + "k" : String(Math.round(v));
  const pct = (v) => Math.round(v * 100) + "%";
  const what = k === "a" ? "writes" : "reads";
  const tip = (s1, i) => {
    if (s1.v == null) return [html`<span class="k">at this pace</span> ~${n(pace * (i + 1))} by the end of it · ${pct(pace * (i + 1) / month)} of the free ${n(month)}`];
    const upto = slots.slice(0, i + 1).reduce((a, x) => a + (x.v || 0), 0);
    return [html`<span class="k">${what}</span> ${s1.v.toLocaleString("en")} · ${pct(s1.v / free)} of the ${n(free)} free share`,
      s1.v > free ? html`<span class="k">over it by</span> ${n(s1.v - free)}` : html`<span class="k">under it by</span> ${n(free - s1.v)}`,
      html`<span class="k">month to here</span> ${n(upto)} · ${pct(upto / month)} of the free ${n(month)}`];
  };
  const lines = [{ v: free, colour: "var(--ink)", text: `${n(free)} free a day` }];
  if (cap) lines.push({ v: cap, colour: "var(--bad)", text: `${n(cap)} a day: 2M a month, $9` });
  if (used) lines.push({ v: pace, colour: "var(--wrf)", text: `pace ${n(pace)} a day → ${n(end)}`, solid: true });
  const over = slots.filter((s1) => s1.v > free).length, had = slots.filter((s1) => s1.v != null).length;
  const cost = bill(end);
  return html`<div class="panel s6"><h2>${title} · ${m.from.slice(5)} to ${m.to.slice(5)} (UTC)</h2>
    <${MonthBars} sp=${sp} slots=${slots} min=${Math.max((cap || free) * 1.15, pace * 1.1)} lines=${lines} tip=${tip}
      colour=${(v) => cap && v > cap ? "var(--bad)" : v > free ? "var(--warn)" : "var(--accent)"} aria=${`${title}: this month so far ${n(used)}, at this pace ${n(end)}`}/>
    <p class="whytxt">This month so far ${used.toLocaleString("en")} (Cloudflare's count); at this pace ${n(end)} by ${m.to.slice(5)} of the free ${n(month)}: ${cost ? `$${cost.toFixed(2)}` : "free"}.
      Over the day's free share on ${over} of ${had} days${cap ? `, over the 2M line on ${slots.filter((s1) => s1.v > cap).length}` : ""}.</p></div>`;
}

// Cloudflare's billing month (the 7th to the 7th, admin's month) as one slot a UTC day: its day, the spending day d, and
// value(d); the days to come with none.
function monthSlots(sp, value) {
  const start = Date.parse(sp.month.from + "T00:00:00Z"), byDay = new Map(sp.days.map((d) => [d.day, d]));
  return Array.from({ length: Math.round(sp.month.length) }, (_, i) => {
    const day = new Date(start + i * 864e5).toISOString().slice(0, 10), d = byDay.get(day);
    return { day, d, v: d ? value(d) : null };
  });
}

// A month's bars (the Costs charts): each day's date under it, lines across (dashed, or solid) with haloed labels (the
// last solid one on the left so it doesn't meet the others'), and a hover box per day: its date, then tip(slot, i).
function MonthBars({ sp, slots, min, colour, lines, tip, aria }) {
  const [hv, setHv] = useState(null);
  const svgRef = useRef(null);
  const today = sp.days.length ? sp.days[sp.days.length - 1].day : "";
  const X0 = 15, X1 = 495, B = 150, w = (X1 - X0) / slots.length;
  const top = Math.max(min, ...slots.map((s1) => s1.v || 0));
  const y = (v) => B - v / top * 130;
  const at = (ev) => {
    const r = svgRef.current.getBoundingClientRect(), i = Math.floor(((ev.clientX - r.left) * 500 / r.width - X0) / w);
    return i < 0 || i >= slots.length ? null : { i, left: ev.clientX - r.left, top: ev.clientY - r.top, w: r.width };
  };
  let box = null;
  if (hv) {
    const s1 = slots[hv.i];
    const date = new Date(s1.day + "T00:00:00Z").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
    const rows = [html`<b>${date}</b>${s1.day === today ? " · today so far" : s1.v == null ? " · to come" : ""}`, ...tip(s1, hv.i)];
    box = html`<div class="ttip" style=${`${hv.left > hv.w * 0.55 ? `right:${hv.w - hv.left + 12}px` : `left:${hv.left + 12}px`};top:${Math.max(0, hv.top - 10)}px`}>${rows.map((l1, j) => html`${j ? html`<br/>` : null}${l1}`)}</div>`;
  }
  return html`<div class="opsc"><svg ref=${svgRef} class="chart" viewBox="0 0 500 172" onPointerMove=${(ev) => setHv(at(ev))} onPointerDown=${(ev) => setHv(at(ev))}
      onPointerLeave=${() => setHv(null)} role="img" aria-label=${aria}>
    ${hv ? html`<rect x=${X0 + hv.i * w} y="8" width=${w} height=${B - 8} fill="var(--line)"/>` : null}
    ${slots.map((s1, i) => {
      if (s1.v == null) return html`<rect x=${X0 + i * w + 2} y=${B - 1} width=${Math.max(w - 4, 2)} height="1" fill="var(--line)"/>`;
      const h = Math.max(B - y(s1.v), 1);
      return html`<rect x=${X0 + i * w + 2} y=${B - h} width=${Math.max(w - 4, 2)} height=${h} rx="2" opacity=${hv && hv.i !== i ? 0.6 : 1} fill=${colour(s1.v)}/>`;
    })}
    ${slots.map((s1, i) => html`<text x=${X0 + i * w + w / 2} y="166" text-anchor="middle" style=${s1.day === today ? "fill:var(--ink)" : ""}>${+s1.day.slice(8)}</text>`)}
    ${lines.map((l1) => html`<line x1=${X0} x2=${X1} y1=${y(l1.v)} y2=${y(l1.v)} stroke=${l1.colour} stroke-width=${l1.solid ? 2 : 1.5} stroke-dasharray=${l1.solid ? null : "5 4"}/>
      <text x=${l1.solid ? X0 + 2 : X1} y=${y(l1.v) - 5} text-anchor=${l1.solid ? "start" : "end"} paint-order="stroke" stroke="var(--panel)" stroke-width="4"
        stroke-linejoin="round" style=${`fill:${l1.colour}`}>${l1.text}</text>`)}
  </svg>${box}</div>`;
}

function Costs({ ad, now }) {
  const o = ad && ad.overview;
  if (!o) return html`<p class="empty">Reading admin's data…</p>`;
  const folders = Object.entries((o.storage || {}).folders || {}).map(([k, v]) => [k, v[0], v[1]]).sort((a, b) => b[1] - a[1]);
  const total = folders.reduce((a, f) => a + f[1], 0);
  // Every folder (the owner, 20.11.2026: "Do we show here all files/folders?": 18 of 154 shown, 51 GB hidden), with its files.
  const folderRow = ([k, b, n]) => html`<div style="display:grid;grid-template-columns:minmax(0,1fr) 1fr 80px 72px;gap:8px;align-items:center;font-size:13.5px">
      <span style="overflow-wrap:anywhere">${k}</span><div class="hbar"><i style=${`width:${(b / folders[0][1] * 100).toFixed(1)}%`}></i></div><span class="num" style="text-align:right">${(b / 1e9).toFixed(2)} GB</span><span class="num" style="text-align:right;color:var(--muted)">${n != null ? n.toLocaleString("en") : ""}</span></div>`;
  const storeCost = Math.max(0, total / 1e9 - 10) * 0.015;
  const om = o.openMeteo || [];
  const days = [...new Set(om.map((x) => x.day))].sort((a, b) => a - b).slice(-14);
  const perDay = days.map((d) => om.filter((x) => x.day === d && !x.kind.includes("refused")).reduce((a, x) => a + (x.weight || 0), 0));
  const month = perDay.reduce((a, v) => a + v, 0);
  const maxDay = Math.max(9000, ...perDay);
  return html`<div class="grid">
    ${spendPanels(o.spend, storeCost)}
    <div class="panel s3"><h2>File store</h2><div class="big">${(total / 1e9).toFixed(1)} <small>GB · 10 free</small></div><p class="whytxt">$0.015 a GB a month after 10; counted ${o.storage && o.storage.time ? hrs(now - o.storage.time) + " ago" : "–"}.</p></div>
    <div class="panel s3"><h2>Open-Meteo weight</h2><div class="big">${month.toLocaleString("en")} <small>14 days</small></div><p class="whytxt">Admins' vs OM and the server's comparisons; none for riders.</p></div>
    <div class="panel s3"><h2>Database</h2><div class="big">${((o.collector || []).find((c) => c.key === "readings_rows") || {}).value?.toLocaleString?.("en") ?? "–"} <small>readings rows</small></div><p class="whytxt">Free tier: 5M rows read a day.</p></div>
    <div class="panel s6"><h2>File store by folder · ${folders.length}</h2><div style="display:grid;gap:6px">${folders.slice(0, 18).map(folderRow)}</div>
      ${folders.length > 18 ? html`<details style="margin-top:6px"><summary style="cursor:pointer;font-size:13.5px;color:var(--muted)">${folders.length - 18} more folders · ${(folders.slice(18).reduce((a, f) => a + f[1], 0) / 1e9).toFixed(2)} GB</summary>
        <div style="display:grid;gap:6px;margin-top:6px">${folders.slice(18).map(folderRow)}</div></details>` : null}</div>
    <div class="panel s6"><h2>Open-Meteo weight per day · 14 days (UTC)</h2>
      <svg class="chart" viewBox="0 0 500 180">${perDay.map((v, i) => { const h = v / maxDay * 140; return html`<rect x=${20 + i * 34} y=${155 - h} width="24" height=${Math.max(h, 1)} rx="3" fill="var(--accent)"><title>${v}</title></rect>`; })}
        <line x1="15" x2="495" y1=${155 - 9000 / maxDay * 140} y2=${155 - 9000 / maxDay * 140} stroke="var(--warn)" stroke-dasharray="4 4"/><text x="490" y=${150 - 9000 / maxDay * 140} text-anchor="end">9,000 a day</text></svg></div>
    <div class="panel s12"><h2>Home machine</h2><div class="kv"><span>Power, the Tower (estimated)</span><span>~110 W · ~€15 a month</span><span>The Mac mini, when it runs everything</span><span>~25 W · ~€3 a month</span></div></div>
  </div>`;
}

