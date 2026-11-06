// MARK: Quality

function Quality({ ad, now }) {
  const o = ad && ad.overview;
  if (!o) return html`<p class="empty">${ad && ad.error ? ad.error : "Reading admin's data…"}</p>`;
  const rows = (o.ours || {}).rows || [];
  const models = [...new Set(rows.map((r) => r.model))];
  const cell = (m, lead) => rows.find((r) => r.model === m && r.lead === lead);
  const limit = (m) => m.endsWith("#temp") ? 0.5 : 0.3;
  const over = models.filter((m) => { const c = cell(m, "0-48"); return c && c.n >= 100 && c.mae > limit(m); });
  const rs = o.rainSkill || [];
  const lastRs = rs[rs.length - 1];
  const sets = lastRs ? Object.entries(lastRs.sets).sort((a, b) => b[1].fss - a[1].fss) : [];
  const skill = ((o.alerts || {}).skill || []);
  const csi = (m, lead) => { const s = skill.find((x) => x.method === m && x.lead === lead); return s ? s.hits / Math.max(1, s.hits + s.misses + s.falses) : null; };
  const W = 400, H = 170, X0 = 30, Y0 = 10, Y1 = 150;
  const lines = [["rain-now+1", "var(--ok)"], ["composite", "var(--home)"], ["weighted", "var(--wrf)"], ["icon-eu-wx", "var(--warn)"]];
  return html`<div class="grid">
    <div class="panel s3"><h2>Ours vs Open-Meteo</h2><div class="big" style=${over.length ? "color:var(--warn)" : "color:var(--ok)"}>${over.length} <small>over the limit</small></div><p class="whytxt">Same run, first 48 h: 0.5 °C air, 0.3 m/s wind.</p></div>
    <div class="panel s3"><h2>Rain map skill · last hour</h2><div class="big">${lastRs && lastRs.sets.weighted ? lastRs.sets.weighted.fss.toFixed(2) : "–"} <small>FSS, weighted</small></div><p class="whytxt">Best now: ${sets[0] ? sets[0][0] + " " + sets[0][1].fss.toFixed(2) : "–"}</p></div>
    <div class="panel s3"><h2>Rain alerts</h2><div class="big">${csi("blend", 60) != null ? csi("blend", 60).toFixed(2) : "–"} <small>CSI at 60 min</small></div><p class="whytxt">Blend method, 7 days.</p></div>
    <div class="panel s3"><h2>Spots on our data</h2><div class="big">${(o.stations || {}).total != null ? "" : ""}${rows.length ? models.length : "–"} <small>models compared</small></div><p class="whytxt">Last compared ${rows.length ? hrs(now - Math.max(...rows.map((r) => r.last || 0))) + " ago" : "–"}.</p></div>
    <div class="panel s8"><h2>Ours vs Open-Meteo, same run · mean difference (bias: ours higher +)</h2><div class="tablewrap"><table>
      <thead><tr><th>Model</th><th class="r">0-2 days</th><th class="r">2-5 days</th><th class="r">5+ days</th><th class="r">Spots</th><th></th></tr></thead>
      <tbody>${models.sort((a, b) => ((cell(b, "0-48") || {}).mae || 0) - ((cell(a, "0-48") || {}).mae || 0)).map((m) => {
        const u = m.endsWith("#temp") ? " °C" : m === "waves" ? " m" : " m/s";
        const show = (c) => c ? html`${c.mae.toFixed(2)}${u} <span class="whytxt">(${c.bias >= 0 ? "+" : ""}${c.bias.toFixed(2)})</span>` : "–";
        const c0 = cell(m, "0-48");
        const bad = c0 && c0.n >= 100 && c0.mae > limit(m);
        return html`<tr><td>${m.replace("#temp", " air").replace(/_/g, " ")}</td><td class="r num" style=${bad ? "color:var(--warn)" : ""}>${show(c0)}</td><td class="r num">${show(cell(m, "48-120"))}</td><td class="r num">${show(cell(m, "120+"))}</td>
          <td class="r num">${c0 ? c0.spots : "–"}</td><td><span class=${"st " + (bad ? "wait" : "ok")}>${bad ? "look" : "fine"}</span></td></tr>`;
      })}</tbody></table></div></div>
    <div class="panel s4"><h2>Rain map vs radar · FSS by hour (24 h)</h2>
      <svg class="chart" viewBox="0 0 ${W} ${H}">
        ${[0, 0.5, 1].map((v) => html`<line x1=${X0} x2=${W - 5} y1=${Y1 - v * (Y1 - Y0)} y2=${Y1 - v * (Y1 - Y0)} stroke="var(--line)"/><text x=${X0 - 5} y=${Y1 - v * (Y1 - Y0) + 4} text-anchor="end">${v}</text>`)}
        ${lines.map(([k, col]) => { const pts = rs.slice(-24).map((r, i, a) => r.sets[k] ? `${X0 + i / Math.max(a.length - 1, 1) * (W - X0 - 5)},${Y1 - r.sets[k].fss * (Y1 - Y0)}` : null).filter(Boolean);
          return pts.length > 1 ? html`<polyline points=${pts.join(" ")} fill="none" stroke=${col} stroke-width="2"/>` : null; })}
      </svg><div class="legend">${lines.map(([k, c]) => html`<span style=${"--c:" + c}>${k}</span>`)}</div></div>
    <p class="note s12" style="margin:0">The rain nowcast's full charts, the blend's weights and the satellite clouds against the airports: <a href="#rain" style="color:var(--accent)">Rain &amp; clouds</a>.</p>
  </div>`;
}

// MARK: Rain & clouds (the owner, 01.11.2026: admin's rain charts "make same in ops, but in separate tab - rain and
// clouds. For clouds lets make also graphical representation how our clouds correct"). The rain from admin's overview
// (rain_now.py's skill, the alerts); the clouds from clouds_score.py (runner/clouds-score.json, on the sat feed).

const CLOUD_BANDS = [["night", "night", "var(--wrf)"], ["low", "low sun", "var(--home)"], ["day", "day", "var(--accent)"]];
const dayMonth = (t) => new Date(t * 1000).toLocaleDateString("en-GB", { timeZone: ZONE, day: "2-digit", month: "2-digit" });

// Lines over time: series [{name, color, dash, width, points: [[t, v, tip]]}], y from 0 to top, shade [[t0, t1, opacity]].
function TimeChart({ series, x0, x1, top = 1, pct, shade = [], label = clock, H = 190, ticks }) {
  const W = 700, L = 46, R = 12, T = 10, B = 26;
  const x = (t) => L + (t - x0) / Math.max(x1 - x0, 1) * (W - L - R), y = (v) => T + (1 - Math.max(0, Math.min(top, v)) / top) * (H - T - B);
  const step = [3600, 7200, 10800, 21600, 43200].find((v) => (x1 - x0) / v <= 7) || 86400;
  ticks = ticks || Array.from({ length: Math.floor(x1 / step) - Math.ceil(x0 / step) + 1 }, (_, i) => (Math.ceil(x0 / step) + i) * step);
  return html`<svg class="chart" viewBox=${`0 0 ${W} ${H}`}>
    ${shade.map(([a, b, o]) => html`<rect x=${x(a)} y=${T} width=${Math.max(x(b) - x(a), 1)} height=${H - T - B} fill="var(--line)" opacity=${o}/>`)}
    ${[0, 0.25, 0.5, 0.75, 1].map((f) => html`<line x1=${L} x2=${W - R} y1=${y(f * top)} y2=${y(f * top)} stroke="var(--line)"/>
      <text x=${L - 6} y=${y(f * top) + 4} text-anchor="end">${pct ? Math.round(f * top * 100) + "%" : (f * top).toFixed(2)}</text>`)}
    ${ticks.map((t) => html`<text x=${x(t)} y=${H - 8} text-anchor="middle">${label(t)}</text>`)}
    ${series.map((s) => s.points.length ? html`<g>
      <path d=${s.points.map(([t, v], k) => (k ? "L" : "M") + x(t).toFixed(1) + " " + y(v).toFixed(1)).join(" ")} fill="none" stroke=${s.color}
        stroke-width=${s.width || 2} stroke-dasharray=${s.dash || null}/>
      ${s.points.map(([t, v, tip]) => html`<circle cx=${x(t)} cy=${y(v)} r="2.5" fill=${s.color}><title>${tip}</title></circle>`)}</g>` : null)}
  </svg>`;
}

const Legend = ({ items }) => html`<div class="legend">${items.map(([name, c, dash]) => html`<span style=${"--c:" + c}>${name}${dash ? " (dashed)" : ""}</span>`)}</div>`;

function RainSection({ o, now }) {
  const rs = o.rainSkill || [];
  const al = o.alerts || {};
  const avg = (name, key) => { const v = rs.slice(-24).map((r) => r.sets[name] && r.sets[name][key]).filter((x) => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  const sets = [["rain-now+1", "var(--ok)", null, 3], ["rain-now+3", "var(--ok)", "6 4", 2], ["rain-now+6", "var(--ok)", "2 3", 2], ["composite", "var(--home)", null, 2],
    ["weighted", "var(--wrf)", null, 2], ["icon-eu-wx", "var(--warn)", "8 3 2 3", 1.5], ["mix-wx", "var(--mac)", null, 2]];
  const series = sets.map(([name, color, dash, width]) => ({ name, color, dash, width, points: rs.filter((r) => r.sets[name] && r.sets[name].fss != null)
    .map((r) => { const v = r.sets[name]; return [r.hour, v.fss, `${name} ${clock(r.hour)} · FSS ${v.fss.toFixed(2)}${v.wet != null ? " · wet x" + v.wet.toFixed(1) : ""}${v.off != null ? " · off " + v.off + " km" : ""}`]; }) }));
  const leads = [1, 2, 3, 4, 5, 6, 7].map((l) => [l, avg("rain-now+" + l, "fss")]).filter(([, f]) => f != null);
  const base = avg("composite", "fss");
  const latest = rs.length ? rs[rs.length - 1].sets : {};
  const sk = al.skill || [];
  const csi = (m, lead) => { const r = sk.find((x) => x.method === m && x.lead === lead); const n = r ? (r.hits || 0) + (r.misses || 0) + (r.falses || 0) : 0; return n ? { v: r.hits / n, n } : null; };
  const LEADS = [30, 60, 90, 120], methods = [["share", "var(--home)", null], ["pixel", "var(--warn)", "6 4"], ["model", "var(--wrf)", "2 3"], ["blend", "var(--ok)", "8 3 2 3"]];
  const W = 700, H = 190, L = 46, T = 10, B = 26, cx = (i) => L + 40 + i * (W - L - 92) / 3, cy = (v) => T + (1 - v) * (H - T - B);
  const events = LEADS.map((l) => (csi("share", l) || {}).n || 0), most = Math.max(...events, 1);
  const sent = (kind) => (al.sent || []).find((x) => x.kind === kind) || { ok: 0, failed: 0 };
  const failed = ["rain", "wind", "test"].reduce((a, k) => a + (sent(k).failed || 0), 0);
  const today = Math.floor(now / 86400), days = Array.from({ length: 14 }, (_, i) => today - 13 + i);
  const push = days.map((d) => { const v = { rain: 0, wind: 0, test: 0, failed: 0 }; for (const r of (o.pushes || []).filter((r) => r.day === d)) { v.failed += r.failed || 0; if (v[r.kind] != null) v[r.kind] += r.ok || 0; } return v; });
  const pmax = Math.max(1, ...push.map((v) => v.rain + v.wind + v.test + v.failed)), PH = 130, pw = (W - L - 12) / 14;
  const kinds = [["rain", "var(--home)"], ["wind", "var(--ok)"], ["test", "var(--faint)"], ["failed", "var(--bad)"]];
  return html`
    <div class="panel s12"><h2>Rain and wind alerts</h2><div class="kv">
      <span>Rain alerts</span><span>${al.error ? al.error : `${(al.rain || {}).phones ?? 0} phones · ${(al.rain || {}).places ?? 0} places`}</span>
      <span>Wind alerts</span><span>${(al.wind || {}).phones ?? 0} phones · ${(al.wind || {}).spots ?? 0} spots (our stations)</span>
      ${al.radar ? html`<span style=${now - al.radar.time > 2700 ? "color:var(--warn)" : ""}>Radar checked</span><span>${clock(al.radar.time)} · ${hrs(now - al.radar.time)} ago</span>` : null}
      <span style=${failed ? "color:var(--warn)" : ""}>Pushes, 24 h</span><span>rain ${sent("rain").ok || 0} · wind ${sent("wind").ok || 0} · test ${sent("test").ok || 0}${failed ? ` · ${failed} failed` : ""}</span>
      ${al.last ? html`<span>Last push</span><span>${al.last.kind} · ${al.last.place || ""} · ${hrs(now - al.last.time)} ago</span>` : null}
      ${(al.errors || []).map((e) => html`<span style="color:var(--bad)">${e.kind} ${clock(e.time)}</span><span>${(e.place || "") + " · " + e.status + (e.reason ? " " + e.reason : "")}</span>`)}
    </div></div>
    <div class="panel s12"><h2>Rain map vs radar · each hour, ${Math.round(rs.length)} h</h2>
      <p class="note" style="margin:0 0 6px">FSS at 40 km, 0.5 mm/h: 1 perfect, 0.5 useful. rain-now +N = our blend made N hours before, composite = the forecast as the map draws it, weighted = the sets by their skill.</p>
      ${rs.length ? html`<${TimeChart} series=${series} x0=${rs[0].hour} x1=${Math.max(rs[rs.length - 1].hour, rs[0].hour + 3600)} />
      <${Legend} items=${sets} />
      <div class="kv" style="margin-top:10px">
        ${leads.length ? html`<span>Blend, hours ahead (24 h)</span><span>${leads.map(([l, f]) => "+" + l + " " + f.toFixed(2)).join(" · ")}${base != null ? " · forecast " + base.toFixed(2) : ""}</span>` : null}
        ${o.rainWeights ? html`<span>Weights by skill</span><span>${Object.entries(o.rainWeights).map(([k, v]) => k + " " + v.toFixed(2)).join(" · ")}</span>` : null}
        ${["rain-now+1", "weighted", "composite", "mix-wx", "mix-world-wx", "metnordic-1km-wx", "metnordic-wx", "icon-eu-wx", "ecmwf"].filter((n) => latest[n]).map((n) => {
          const v = latest[n], f = avg(n, "fss"), w = avg(n, "wet");
          return html`<span>${n}</span><span>FSS ${v.fss != null ? v.fss.toFixed(2) : "–"}${f != null ? ` (24 h ${f.toFixed(2)})` : ""} · wet x${v.wet != null ? v.wet.toFixed(1) : "–"}${w != null ? ` (24 h x${w.toFixed(1)})` : ""} · off ${v.off != null ? v.off + " km" : "–"}${v.lead != null ? ` · ${v.lead} h ahead` : ""}</span>`;
        })}
      </div>` : html`<p class="empty">No rain skill yet.</p>`}</div>
    <div class="panel s6"><h2>Rain nowcast · 7 days, CSI by time ahead</h2>
      <p class="note" style="margin:0 0 6px">1 perfect, 0 useless. share = the alerts, pixel = before, model = MET Nordic, blend = both. Bars: events judged.</p>
      <svg class="chart" viewBox=${`0 0 ${W} ${H}`}>
        ${[0, 0.25, 0.5, 0.75, 1].map((v) => html`<line x1=${L} x2=${W - 12} y1=${cy(v)} y2=${cy(v)} stroke="var(--line)"/><text x=${L - 6} y=${cy(v) + 4} text-anchor="end">${v.toFixed(2)}</text>`)}
        ${LEADS.map((l, i) => { const h = events[i] / most * (H - T - B) * 0.5; return html`<rect x=${cx(i) - 9} y=${cy(0) - h} width="18" height=${h} rx="2" fill="var(--line)"><title>${events[i]} events at ${l} min</title></rect>
          <text x=${cx(i)} y=${H - 8} text-anchor="middle">${l} min</text>`; })}
        ${methods.map(([m, c, dash]) => { const p = LEADS.map((l, i) => [i, csi(m, l)]).filter(([, v]) => v); return p.length ? html`<g>
          <path d=${p.map(([i, v], k) => (k ? "L" : "M") + cx(i) + " " + cy(v.v)).join(" ")} fill="none" stroke=${c} stroke-width="2" stroke-dasharray=${dash}/>
          ${p.map(([i, v]) => html`<circle cx=${cx(i)} cy=${cy(v.v)} r="3.5" fill=${c}><title>${m} ${v.v.toFixed(2)} · ${v.n} events</title></circle>`)}</g>` : null; })}
      </svg><${Legend} items=${methods} />
      <div class="kv" style="margin-top:10px">${["1mm", "4mm"].map((th) => { const parts = ["share", "pixel", "model"].map((m) => { const c = csi(m + " " + th, 60); return c ? m + " " + c.v.toFixed(2) : null; }).filter(Boolean);
        const b = csi("share " + th, 60); return parts.length ? html`<span>${th.replace("mm", " mm/h")}, 60 min</span><span>${parts.join(" · ")}${b ? ` (${b.n} events)` : ""}</span>` : null; })}</div></div>
    <div class="panel s6"><h2>Pushes per day · 14 days (UTC)</h2>
      <svg class="chart" viewBox=${`0 0 ${W} ${PH}`}>
        ${[0, 0.5, 1].map((f) => html`<line x1=${L} x2=${W - 12} y1=${T + (1 - f) * (PH - T - B)} y2=${T + (1 - f) * (PH - T - B)} stroke="var(--line)"/><text x=${L - 6} y=${T + (1 - f) * (PH - T - B) + 4} text-anchor="end">${Math.round(f * pmax)}</text>`)}
        ${push.map((v, i) => { let acc = 0; return html`<g>${kinds.map(([k, c]) => { const h = v[k] / pmax * (PH - T - B); acc += h;
          return h ? html`<rect x=${L + i * pw + 3} y=${T + (PH - T - B) - acc} width=${pw - 6} height=${h} fill=${c}><title>${dayMonth(days[i] * 86400)} ${k} ${v[k]}</title></rect>` : null; })}
          ${i % 2 ? null : html`<text x=${L + (i + 0.5) * pw} y=${PH - 8} text-anchor="middle">${dayMonth(days[i] * 86400)}</text>`}</g>`; })}
      </svg><${Legend} items=${kinds} /></div>`;
}

function CloudSection({ sc, now }) {
  if (!sc || !sc.frames) return html`<div class="panel s12"><h2>Satellite clouds vs airports</h2><p class="empty">No scorecard yet (clouds_score.py on the Mac, hourly).</p></div>`;
  const frames = sc.frames, stations = (sc.stations || []).map((s, i) => ({ code: s[0], name: s[1], lat: s[2], i }));
  const verdict = (ch) => ch === "." ? null : { band: CLOUD_BANDS[Math.floor((ch.charCodeAt(0) - 97) / 4)][0], v: (ch.charCodeAt(0) - 97) % 4 };
  const WHAT = ["overcast, drawn cloud", "overcast, drawn clear", "clear, drawn clear", "clear, drawn cloud"];
  const COLOR = ["var(--ok)", "var(--bad)", "var(--ok)", "var(--warn)"];
  const per = frames.map(([t, , cells]) => { const c = [0, 0, 0, 0]; for (const ch of cells) { const v = verdict(ch); if (v) c[v.v]++; } return [t, c]; });
  const share = (a, b) => (a + b >= 3 ? a / (a + b) : null);  // a share from 3 reports: one clear airport isn't 100 %
  const missed = per.map(([t, c]) => [t, share(c[1], c[0]), `${clock(t)} · overcast drawn clear ${c[1]} of ${c[0] + c[1]}`]).filter(([, v]) => v != null);
  const falses = per.map(([t, c]) => [t, share(c[3], c[2]), `${clock(t)} · clear drawn cloud ${c[3]} of ${c[2] + c[3]}`]).filter(([, v]) => v != null);
  const right = per.map(([t, c]) => [t, share(c[0] + c[2], c[1] + c[3]), `${clock(t)} · right at ${c[0] + c[2]} of ${c[0] + c[1] + c[2] + c[3]} airports`]).filter(([, v]) => v != null);
  const x0 = frames.length ? frames[0][0] : now - 172800, x1 = frames.length ? Math.max(frames[frames.length - 1][0], x0 + 3600) : now;
  const shade = frames.map(([t, b], k) => b === "day" ? null : [t - 450, (frames[k + 1] ? Math.min(frames[k + 1][0], t + 1800) : t + 900) - 450, b === "night" ? 0.7 : 0.3]).filter(Boolean);
  const errTop = Math.min(1, Math.max(0.25, Math.ceil(Math.max(0, ...missed.map((p) => p[1]), ...falses.map((p) => p[1])) * 4) / 4));
  // The airports as rows, north first, each frame a cell.
  const rows = stations.filter((s) => frames.some((f) => f[2][s.i] && f[2][s.i] !== ".")).sort((a, b) => (b.lat || 0) - (a.lat || 0));
  const W = 700, L = 86, R = 12, T = 4, RH = 15, GH = T + rows.length * RH + 24, cw = (W - L - R) / Math.max(frames.length, 1);
  const gx = (t) => L + (t - x0) / Math.max(x1 - x0, 1) * (W - L - R - cw);
  const days = Object.keys(sc.days || {}).sort();
  const dayT = (d) => Date.UTC(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10)) / 1000 + 43200;
  const daySeries = CLOUD_BANDS.flatMap(([k, name, c]) => [
    { name: name + " overcast drawn clear", color: c, width: 2, points: days.filter((d) => (sc.days[d][k] || [0])[0] >= 10).map((d) => { const v = sc.days[d][k]; return [dayT(d), v[1] / v[0], `${d} ${name}: overcast drawn clear ${v[1]} of ${v[0]}`]; }) },
    { name: name + " clear drawn cloud", color: c, dash: "5 4", width: 1.5, points: days.filter((d) => (sc.days[d][k] || [0, 0, 0])[2] >= 10).map((d) => { const v = sc.days[d][k]; return [dayT(d), v[3] / v[2], `${d} ${name}: clear drawn cloud ${v[3]} of ${v[2]}`]; }) }]);
  const dayTop = Math.min(1, Math.max(0.25, Math.ceil(Math.max(0, ...daySeries.flatMap((s) => s.points.map((p) => p[1]))) * 4) / 4));
  const last2 = days.slice(-2).reverse();
  return html`
    <div class="panel s12"><h2>Satellite clouds vs airports · each frame, ${Math.round((x1 - x0) / 3600)} h</h2>
      <p class="note" style="margin:0 0 6px">Each published frame at :15 and :45 against the METARs of ${rows.length} airports round the Baltic (:20, :50): overcast (BKN, OVC) drawn clear, clear (CAVOK, NSC, FEW only) drawn cloud (judged on the ~5 km round each airport: a quarter cloudy for overcast, three quarters for clear), and right overall. Shaded: night at Riga (dark), low sun (light). A frame's share from 3 reports. Scored 90 minutes after each frame, as published then.</p>
      <${TimeChart} series=${[{ name: "right", color: "var(--ok)", width: 2.5, points: right }]} x0=${x0} x1=${x1} pct shade=${shade} H=${150} />
      <${TimeChart} series=${[{ name: "overcast drawn clear", color: "var(--bad)", points: missed }, { name: "clear drawn cloud", color: "var(--warn)", dash: "5 4", points: falses }]} x0=${x0} x1=${x1} top=${errTop} pct shade=${shade} />
      <${Legend} items=${[["right, all reports", "var(--ok)"], ["overcast drawn clear, of overcast reports", "var(--bad)"], ["clear drawn cloud, of clear reports", "var(--warn)", true]]} /></div>
    <div class="panel s12"><h2>Airport by airport · each frame</h2>
      <svg class="chart" viewBox=${`0 0 ${W} ${GH}`}>
        ${shade.map(([a, b, op]) => html`<rect x=${gx(a + 450)} y=${T} width=${Math.max(gx(b + 450) - gx(a + 450), 1)} height=${rows.length * RH} fill="var(--line)" opacity=${op}/>`)}
        ${rows.map((s, r) => html`<g><text x=${L - 6} y=${T + r * RH + RH - 3} text-anchor="end">${s.name}</text>
          ${frames.map(([t, , cells]) => { const v = verdict(cells[s.i] || "."); return v ? html`<rect x=${gx(t) + 0.5} y=${T + r * RH + 1.5} width=${Math.max(cw - 1, 1)} height=${RH - 3} rx="1.5"
            fill=${COLOR[v.v]} opacity=${v.v % 2 ? 1 : 0.45}><title>${s.name} ${dayMonth(t)} ${clock(t)} (${v.band === "low" ? "low sun" : v.band}): ${WHAT[v.v]}</title></rect>` : null; })}</g>`)}
        ${Array.from({ length: Math.floor(x1 / 21600) - Math.ceil(x0 / 21600) + 1 }, (_, i) => (Math.ceil(x0 / 21600) + i) * 21600).map((t) => html`<text x=${gx(t) + cw / 2} y=${GH - 8} text-anchor="middle">${clock(t)}</text>`)}
      </svg>
      <${Legend} items=${[["right (overcast drawn cloud, clear drawn clear)", "var(--ok)"], ["overcast drawn clear", "var(--bad)"], ["clear drawn cloud", "var(--warn)"]]} />
      <p class="whytxt" style="margin:6px 0 0">Empty: no verdict (no report, or scattered cloud). Hover a cell for its time and report.</p></div>
    <div class="panel s12"><h2>By day · ${days.length} UTC days</h2>
      ${days.length ? html`<${TimeChart} series=${daySeries} x0=${dayT(days[0]) - 43200} x1=${dayT(days[days.length - 1]) + 43200} top=${dayTop} pct label=${dayMonth} ticks=${days.map(dayT)} />
      <${Legend} items=${CLOUD_BANDS.map(([, name, c]) => [name, c])} />
      <p class="whytxt" style="margin:6px 0 0">Solid: overcast drawn clear (of overcast reports). Dashed: clear drawn cloud (of clear reports). A day shows from 10 reports in its band.</p>
      <div class="kv" style="margin-top:10px">${last2.flatMap((d) => CLOUD_BANDS.filter(([k]) => sc.days[d][k]).map(([k, name]) => { const v = sc.days[d][k];
        const p = (a, b) => (b ? Math.round(100 * a / b) + "%" : "–");
        return html`<span>${d.slice(8)}.${d.slice(5, 7)} ${name}</span><span>overcast drawn clear ${p(v[1], v[0])} of ${v[0]} · clear drawn cloud ${p(v[3], v[2])} of ${v[2]}</span>`; }))}
        ${(sc.flags || []).map((g) => html`<span style="color:var(--warn)">Off its two weeks</span><span>${g.day.slice(8)}.${g.day.slice(5, 7)} ${g.band === "low" ? "low sun" : g.band}: ${g.what} ${Math.round(100 * g.share)}% (usual ${Math.round(100 * g.usual)}%)</span>`)}
      </div>` : html`<p class="empty">No days yet.</p>`}</div>`;
}

function RainClouds({ ad, now }) {
  const o = ad && ad.overview;
  const sat = ((ad && ad.feeds) || []).find((x) => x.key === "sat");
  return html`<div class="grid">
    <${CloudSection} sc=${sat && sat.score} now=${now} />
    ${o ? html`<${RainSection} o=${o} now=${now} />` : html`<p class="empty s12">${ad && ad.error ? ad.error : "Reading admin's data…"}</p>`}
  </div>`;
}

