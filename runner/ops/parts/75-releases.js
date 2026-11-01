// MARK: Releases (the owner, 01.11.2026: "make all", from the ops panel ideas): the builds in TestFlight and on riders'
// phones, how fast a new build spreads, crashes per build, the update nudge (iPhone and Android), a test push, What's
// new. Phones from roles (dayBuilds: each phone's build each day, from 30.10.2026; riders only, as Riders counts them),
// TestFlight from /api/builds (uploads), crashes from the overview (crashes.builds), nudges from /api/announce.

const REAL_CRASH = new Set(["crash", "crash_native", "anr"]);
const buildText = (pl, n) => (pl === "android" ? "A" : "") + n;

function riderPhones(roles) {
  const admins = new Set((roles.accounts || []).filter((a) => a.role === "admin").map((a) => a.id));
  return (roles.devices || []).filter((d) => !testKind(d) && d.role !== "admin" && !admins.has(d.account));
}

// Each day's riders by build: {platform: {day: [build numbers]}}, and who ran each build in the last 30 days.
function buildDays(phones, today) {
  const perDay = { ios: {}, android: {} }, ran = { ios: {}, android: {} };
  for (const d of phones) {
    const pl = platformOf(d);
    (d.days || []).forEach((day, k) => {
      const b = +String((d.dayBuilds || [])[k] || "").replace(/\D+/g, "") || 0;
      if (!b || day <= today - 30) return;
      (perDay[pl][day] = perDay[pl][day] || []).push(b);
      (ran[pl][b] = ran[pl][b] || new Set()).add(d.device);
    });
  }
  return { perDay, ran };
}

const SPREAD_COLORS = ["var(--accent)", "var(--home)", "var(--wrf)"];
function Spread({ perDay, ran, pl, today }) {
  // The newest three builds that two or more riders ran: each day's share of riders on that build or newer.
  const tops = Object.keys(ran[pl]).map(Number).filter((b) => ran[pl][b].size >= 2).sort((a, b) => b - a).slice(0, 3);
  const days = Object.keys(perDay[pl]).map(Number).sort((a, b) => a - b);
  if (!tops.length || !days.length) return html`<p class="whytxt">No days yet.</p>`;
  const x0 = (days[0] - 0.5) * 86400, x1 = (today + 0.5) * 86400;
  const series = tops.map((b, i) => ({ name: buildText(pl, b), color: SPREAD_COLORS[i], points: days.map((day) => {
    const list = perDay[pl][day], on = list.filter((x) => x >= b).length;
    return [day * 86400, on / list.length, `${ddmm(day * 86400)}: ${on} of ${list.length} riders on ${buildText(pl, b)} or newer`];
  }) }));
  // A build every rider already has stays at 100 % and says nothing.
  const moving = series.filter((x) => x.points.some((pt) => pt[1] < 0.99));
  if (moving.length) series.splice(0, series.length, ...moving);
  const ticks = days.filter((d) => (today - d) % 7 === 0).map((d) => d * 86400);
  return html`<${TimeChart} series=${series} x0=${x0} x1=${x1} pct=${true} H=${170} ticks=${ticks} label=${ddmm} />
    <${Legend} items=${series.map((s) => [s.name + " or newer", s.color])} />`;
}

function BuildTable({ pl, ran, phones, uploads, crashes, now }) {
  const current = {};
  for (const d of phones) if (platformOf(d) === pl && now - d.last_seen < 7 * 86400 && buildNo(d)) current[buildNo(d)] = (current[buildNo(d)] || 0) + 1;
  const cr = {};
  for (const c of crashes) {
    if ((c.platform === "android") !== (pl === "android")) continue;
    const b = +String(c.build || "").replace(/\D+/g, "") || 0, x = cr[b] || (cr[b] = { phones: 0, n: 0, other: 0 });
    if (REAL_CRASH.has(c.kind)) { x.phones += c.phones; x.n += c.n; } else x.other += c.n;
  }
  const up = Object.fromEntries((pl === "ios" ? uploads : []).filter((u) => u.number).map((u) => [u.number, u]));
  const list = [...new Set([...Object.keys(ran[pl]), ...Object.keys(current), ...Object.keys(cr), ...Object.keys(up)].map(Number))]
    .filter(Boolean).sort((a, b) => b - a).slice(0, 12);
  return html`<div class="tablewrap"><table><thead><tr><th>Build</th>${pl === "ios" ? html`<th class="hide-s">TestFlight</th>` : null}<th class="r">Riders now</th>
    <th class="r">Ran it</th><th class="r">Crashed</th><th class="r hide-s">Other reports</th></tr></thead><tbody>
    ${list.map((b) => { const u = up[b], c = cr[b] || { phones: 0, n: 0, other: 0 }, n = ran[pl][b] ? ran[pl][b].size : 0;
      const rate = n && c.phones ? Math.round(100 * Math.min(c.phones, n) / n) : 0;
      return html`<tr><td><b>${buildText(pl, b)}</b></td>
        ${pl === "ios" ? html`<td class="hide-s">${u ? html`${ddmm(u.uploaded)} ${u.state !== "VALID" ? html`<span class="tag">${String(u.state).toLowerCase()}</span>` : ""}${u.friends ? html` <span class="pill okp">Kite friends</span>` : ""}${u.expired ? html` <span class="tag">expired</span>` : ""}` : html`<span class="whytxt">–</span>`}</td>` : null}
        <td class="r num">${current[b] || "–"}</td><td class="r num">${n || "–"}</td>
        <td class="r num" style=${rate >= 10 ? "color:var(--bad)" : ""} title=${c.n ? `${c.n} crashes and freezes on ${c.phones} phones` : ""}>${c.phones ? `${c.phones} phone${c.phones > 1 ? "s" : ""}${rate ? ` · ${rate} %` : ""}` : "–"}</td>
        <td class="r num hide-s" title="disk writes, CPU, memory and battery reports: warnings, not crashes">${c.other || "–"}</td></tr>`; })}
  </tbody></table></div>`;
}

function Nudge({ ranks, onSent }) {
  const [pl, setPl] = useState("ios");
  const latest = (ranks[pl] || [])[0] || 0;
  const [below, setBelow] = useState("");
  const [title, setTitle] = useState("A new MeteoGust is ready");
  const texts = { ios: "Update in TestFlight for the newest fixes and features.", android: "Update in Google Play for the newest fixes and features." };
  const [body, setBody] = useState("");
  const [who, setWho] = useState(null);
  const input = () => ({ platform: pl, below: Number(below || latest), title, body: body || texts[pl] });
  const check = async () => { const { data } = await adminPostRaw("announce", input()); setWho(data); };
  return html`<div class="setform" style="margin-top:0">
    <div class="btns">${[["ios", "iPhone"], ["android", "Android"]].map(([k, l]) => html`<button class=${"chip" + (pl === k ? " on" : "")} onClick=${() => { setPl(k); setWho(null); setBelow(""); }}>${l}</button>`)}
      <label class="whytxt">to phones on builds before <input style="width:70px" value=${below || latest} onInput=${(e) => { setBelow(e.target.value.replace(/\D+/g, "")); setWho(null); }}/></label></div>
    <input value=${title} onInput=${(e) => { setTitle(e.target.value); setWho(null); }} placeholder="Title"/>
    <input value=${body || texts[pl]} onInput=${(e) => { setBody(e.target.value); setWho(null); }} placeholder="Text"/>
    <div class="btns"><button class="btn" onClick=${check}>Who gets it</button>
      ${who && !who.error && who.reachable.length ? html`<${ActButton} key=${JSON.stringify(input())} primary=${true} label=${`Send to ${who.reachable.length} phone${who.reachable.length > 1 ? "s" : ""}`} ask="Send it now? Click again"
        run=${async () => { const { data } = await adminPostRaw("announce", { ...input(), send: true }); if (!data.error) onSent(); return data; }} />` : null}</div>
    ${who ? who.error ? html`<p class="whytxt" style="color:var(--bad)">${who.error}</p>` : html`<p class="whytxt">${who.reachable.length} on older builds can get it (they have rain or wind alerts on)${who.unreachable.length ? `; ${who.unreachable.length} more without alerts can't` : ""}.
      ${who.reachable.length ? html`<br/>${who.reachable.slice(0, 12).map((r) => `${r.name || "phone"} (${r.build})`).join(", ")}${who.reachable.length > 12 ? "…" : ""}` : null}</p>` : null}
  </div>`;
}

function WhatsNew() {
  const [app, setApp] = useState("ios");
  const [data, setData] = useState(null);
  const [edit, setEdit] = useState(null);
  const [msg, setMsg] = useState("");
  const path = "whats-new" + (app === "android" ? "?app=android" : "");
  useEffect(() => { setData(null); setEdit(null); adminGet(path).then(setData).catch((e) => setData({ error: String(e.message || e) })); }, [app]);
  const r = data && data.releases && data.releases[0];
  return html`<div class="btns">${[["ios", "iPhone"], ["android", "Android"]].map(([k, l]) => html`<button class=${"chip" + (app === k ? " on" : "")} onClick=${() => setApp(k)}>${l}</button>`)}
      ${data && !data.error ? html`<button class="btn" onClick=${() => setEdit(edit === null ? JSON.stringify(data, null, 1) : null)}>${edit === null ? "Edit" : "Close"}</button>` : null}<${Saved} msg=${msg} /></div>
    ${!data ? html`<p class="whytxt">Reading…</p>` : data.error ? html`<p class="whytxt">Can't read: ${data.error}</p>`
      : edit !== null ? html`<textarea style="width:100%;min-height:320px;margin-top:8px;font-family:var(--mono, monospace);font-size:12px" value=${edit} onInput=${(e) => setEdit(e.target.value)}></textarea>
        <div class="btns" style="margin-top:6px"><${ActButton} label="Save" primary=${true} ask="Riders see this. Save? Click again" run=${async () => {
          const body = JSON.parse(edit); const { data: d } = await adminPostRaw(path, body);
          if (d.error) throw new Error(d.error); setMsg("✓ saved"); setEdit(null); setData(await adminGet(path)); return d; }} /></div>`
      : !r ? html`<p class="whytxt">No pages yet.</p>`
      : html`<p class="whytxt" style="margin:8px 0">Shown with build ${r.build} · ${r.date || ""} · ${r.items.length} pages · ${data.releases.length} releases kept</p>
        <div class="wnlist">${r.items.map((it) => html`<div class="wn">${it.image ? (/\.mp4$/.test(it.image) ? html`<video src=${it.image} muted preload="metadata"></video>` : html`<img src=${it.image} alt="" loading="lazy"/>`) : html`<div class="wnph"></div>`}
          <div><b>${it.title}</b><span class="sub">${String(it.text || "").slice(0, 140)}${String(it.text || "").length > 140 ? "…" : ""}</span></div></div>`)}</div>`}
    <p class="whytxt" style="margin:8px 0 0">Only for Kite friends releases, and only with what's in the released build. Pictures are uploaded with call.py whats-new-image.</p>`;
}

function Releases({ roles, builds, ad, now }) {
  const [sent, setSent] = useState(0);
  const [past, setPast] = useState(null);
  useEffect(() => { adminGet("announce").then((d) => setPast(d.announced || [])).catch(() => setPast([])); }, [sent]);
  if (!roles) return html`<p class="empty">Reading riders…</p>`;
  const today = dayNo(now);
  const phones = riderPhones(roles);
  const { perDay, ran } = buildDays(phones, today);
  const { ranks } = ridersOf(roles, now);
  const uploads = (builds && builds.uploads) || [];
  const crashes = (ad && ad.overview && ad.overview.crashes && ad.overview.crashes.builds) || [];
  const newest = uploads.find((u) => u.number);
  return html`<div class="grid">
    <div class="panel s12"><div class="rtiles">
      <div class="ftile"><div class="v num">${newest ? newest.number : "–"}</div><div class="l">newest in TestFlight${newest ? ` · ${agoShort(now - newest.uploaded)} ago` : ""}</div></div>
      <div class="ftile"><div class="v num">${(uploads.find((u) => u.friends) || {}).number || "–"}</div><div class="l">with Kite friends</div></div>
      <div class="ftile"><div class="v num">${ranks.ios[0] || "–"}</div><div class="l">newest iPhone build riders use</div></div>
      <div class="ftile"><div class="v num">${ranks.android[0] ? "A" + ranks.android[0] : "–"}</div><div class="l">newest Android build riders use</div></div>
    </div>${uploads[0] && uploads[0].error ? html`<p class="whytxt">TestFlight: ${uploads[0].error}</p>` : null}</div>
    <div class="panel s6"><h2>How a build spreads · iPhone</h2><${Spread} perDay=${perDay} ran=${ran} pl="ios" today=${today} /></div>
    <div class="panel s6"><h2>How a build spreads · Android</h2><${Spread} perDay=${perDay} ran=${ran} pl="android" today=${today} /></div>
    <div class="panel s6"><h2>iPhone builds</h2><${BuildTable} pl="ios" ran=${ran} phones=${phones} uploads=${uploads} crashes=${crashes} now=${now} /></div>
    <div class="panel s6"><h2>Android builds</h2><${BuildTable} pl="android" ran=${ran} phones=${phones} uploads=${uploads} crashes=${crashes} now=${now} /></div>
    <div class="panel s12"><p class="whytxt" style="margin:0">Each day: the share of riders who opened the app that day on that build or newer (from 30.10.2026). Riders now: phones on it seen this week.
      Ran it: phones that opened it in 30 days. Crashed: phones with a crash or freeze on it, and their share of those who ran it (red from 10 %); both count from 30.10.2026 and at most 30 days. Test phones and admins' phones are left out, except in the crash counts.</p></div>
    <div class="panel s6"><h2>Ask riders to update</h2><${Nudge} ranks=${ranks} onSent=${() => setSent(sent + 1)} />
      <h2 style="margin-top:14px">Sent</h2>${!past ? html`<p class="whytxt">Reading…</p>` : !past.length ? html`<p class="whytxt">Nothing sent yet.</p>`
        : html`<div class="tablewrap"><table><tbody>${past.map((a) => html`<tr><td class="num">${ddmm(a.created)}</td><td><b>${a.title}</b><span class="sub">${a.below ? `builds before ${a.below}` : "test to admins"} · ${a.phones} phones</span></td>
          <td class="r num">${a.sent ? `${a.ok} of ${a.pushes} delivered` : "waiting"}</td></tr>`)}</tbody></table></div>`}
      <div class="btns" style="margin-top:10px"><${ActButton} label="Test push to admin phones" ask="Send a test now? Click again"
        run=${async () => { const { data } = await adminPostRaw("push-test", { send: true }); if (data.error) throw new Error(data.error); setSent(sent + 1); return data; }} /></div></div>
    <div class="panel s6"><h2>What's new</h2><${WhatsNew} /></div>
  </div>`;
}

