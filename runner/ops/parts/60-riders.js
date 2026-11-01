// Riders (the owner, 30.10.2026: "In riders need much more info. Now its only sorting last seen. Not see what app version
// everyone use, can't sort on versions etc. Need more graphs"; mockup docs/mockups/riders-and-sets-2026-10-30.html in the
// app's repo). One row per rider: an account with all its phones, or a phone without an account (a guest). Days active
// come from station-history's device_days (from 30.10.2026), the OS and chosen sets from the check-in of new builds.
const IPHONES = {
  "iPhone12,1": "iPhone 11", "iPhone12,3": "iPhone 11 Pro", "iPhone12,5": "iPhone 11 Pro Max", "iPhone12,8": "iPhone SE 2",
  "iPhone13,1": "iPhone 12 mini", "iPhone13,2": "iPhone 12", "iPhone13,3": "iPhone 12 Pro", "iPhone13,4": "iPhone 12 Pro Max",
  "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max", "iPhone14,4": "iPhone 13 mini", "iPhone14,5": "iPhone 13",
  "iPhone14,6": "iPhone SE 3", "iPhone14,7": "iPhone 14", "iPhone14,8": "iPhone 14 Plus", "iPhone15,2": "iPhone 14 Pro",
  "iPhone15,3": "iPhone 14 Pro Max", "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus", "iPhone16,1": "iPhone 15 Pro",
  "iPhone16,2": "iPhone 15 Pro Max", "iPhone17,1": "iPhone 16 Pro", "iPhone17,2": "iPhone 16 Pro Max", "iPhone17,3": "iPhone 16",
  "iPhone17,4": "iPhone 16 Plus", "iPhone17,5": "iPhone 16e", "iPhone18,1": "iPhone 17 Pro", "iPhone18,2": "iPhone 17 Pro Max",
  "iPhone18,3": "iPhone 17", "iPhone18,4": "iPhone Air",
};
const phoneName = (d) => { const m = d.model || ""; return IPHONES[m] || m.replace(/^Android /, "") || "iPhone"; };
const platformOf = (d) => /^Android/.test(d.model || "") ? "android" : "ios";
// Not riders: our emulator, the Mac's Simulator, Apple's reviewers (as admin's Users tab).
const testKind = (d) => /sdk_gphone|emulator/i.test(d.model || "") ? "emulator"
  : /simulator|^(arm64|x86_64|i386)$/i.test(d.model || "") ? "simulator"
  : !d.account && d.country === "US" && /^iPhone/.test(d.model || "") && (d.last_seen || 0) - (d.first_seen || 0) < 3600 ? "Apple review" : "";
const dayNo = (t) => Math.floor(t / 86400);
// The build's number: iPhone "391", Android "android-18".
const buildNo = (d) => +String(d.build || "").replace(/\D+/g, "") || 0;
const ddmm = (t) => { const d = new Date(t * 1000); return d.getUTCDate() + "." + (d.getUTCMonth() + 1); };
const agoShort = (s) => s < 3600 ? Math.max(1, Math.round(s / 60)) + " min" : s < 86400 ? (s / 3600).toFixed(1) + " h" : Math.round(s / 86400) + (s < 172800 ? " day" : " days");
const PLATFORM_ICON = {
  ios: html`<svg viewBox="0 0 24 24" fill="currentColor" class="picon"><path d="M16.4 12.6c0-2.4 2-3.6 2.1-3.7-1.1-1.7-2.9-1.9-3.5-1.9-1.5-.2-2.9.9-3.7.9-.8 0-1.9-.9-3.2-.8-1.6 0-3.1 1-4 2.4-1.7 3-.4 7.4 1.2 9.8.8 1.2 1.8 2.5 3 2.4 1.2 0 1.7-.8 3.1-.8 1.5 0 1.9.8 3.2.8 1.3 0 2.2-1.2 3-2.4.9-1.4 1.3-2.7 1.3-2.8 0 0-2.5-1-2.5-3.9zM14 5.5c.7-.8 1.1-1.9 1-3-1 0-2.1.7-2.8 1.5-.6.7-1.2 1.8-1 2.9 1 .1 2.1-.6 2.8-1.4z"/></svg>`,
  android: html`<svg viewBox="0 0 24 24" fill="currentColor" class="picon"><path d="M6 9h12v8a1 1 0 0 1-1 1h-1v3h-2v-3h-4v3H8v-3H7a1 1 0 0 1-1-1zM3.5 9.5a1 1 0 0 1 2 0v5a1 1 0 0 1-2 0zm15 0a1 1 0 0 1 2 0v5a1 1 0 0 1-2 0zM7.6 4.2 6.5 2.6l.6-.4 1.2 1.7A6.5 6.5 0 0 1 12 3c1.3 0 2.6.3 3.7.9l1.2-1.7.6.4-1.1 1.6A5.4 5.4 0 0 1 18 8H6a5.4 5.4 0 0 1 1.6-3.8z"/></svg>`,
};

// Riders from the roles answer: accounts with their phones, then phones without an account.
function ridersOf(roles, now) {
  const devices = roles.devices || [];
  const today = dayNo(now);
  // Each platform's builds on riders' phones this week, newest first: latest, one behind, older. Admins' phones
  // don't count, and a build only once two phones have it (an internal TestFlight build on our own phones is ahead
  // of everyone's).
  const admins = new Set((roles.accounts || []).filter((a) => a.role === "admin").map((a) => a.id));
  const ranks = {};
  for (const p of ["ios", "android"]) {
    const n = {};
    for (const d of devices) if (platformOf(d) === p && !testKind(d) && now - d.last_seen < 7 * 86400 && buildNo(d) && !admins.has(d.account) && d.role !== "admin") n[buildNo(d)] = (n[buildNo(d)] || 0) + 1;
    const all = Object.keys(n).map(Number).sort((a, b) => b - a);
    ranks[p] = all.filter((b) => n[b] >= 2).length ? all.filter((b) => n[b] >= 2) : all;
  }
  const buildKind = (d) => { const i = (ranks[platformOf(d)] || []).indexOf(buildNo(d)); return i === 0 ? "latest" : i === 1 ? "behind" : i < 0 && buildNo(d) > ((ranks[platformOf(d)] || [])[0] || 0) ? "latest" : "older"; };
  const person = (phones, account) => {
    // Newest first, real phones before the emulator and Simulator.
    phones = phones.slice().sort((a, b) => (testKind(a) ? 1 : 0) - (testKind(b) ? 1 : 0) || b.last_seen - a.last_seen);
    const main = phones[0] || {};
    const days = [...new Set(phones.flatMap((d) => d.days || []))];
    const last = Math.max(account?.last_seen || 0, ...phones.map((d) => d.last_seen || 0));
    const joined = Math.min(...[account?.created, ...phones.map((d) => d.first_seen)].filter(Boolean));
    return {
      key: account ? "a:" + account.id : "d:" + main.device, account, phones, main,
      name: account ? (account.nickname || [account.first, account.last].filter(Boolean).join(" ") || "–") : (main.nickname || "Guest " + String(main.device || "").slice(0, 6)),
      mail: account ? (account.email || account.apple_email || "") : "",
      platform: platformOf(main), phone: phones.length ? phoneName(main) : "no phone yet", os: main.os || "",
      build: buildNo(main) ? String(buildNo(main)) : main.build || "", buildNo: buildNo(main), buildKind: phones.length ? buildKind(main) : "", country: main.country || "",
      week: [6, 5, 4, 3, 2, 1, 0].map((k) => days.includes(today - k)), days30: days.filter((d) => d > today - 30).length,
      joined: Number.isFinite(joined) ? joined : 0, last, role: account ? account.role : main.role,
      test: !account && testKind(main), sets: [...new Set(phones.flatMap((d) => String(d.sets || "").split(",").filter(Boolean)))],
      shared: phones.reduce((n, d) => n + (d.shared || 0), 0), alerts: phones.reduce((n, d) => n + (d.alerts || 0), 0), dayList: days,
    };
  };
  const accounts = (roles.accounts || []).map((a) => person(devices.filter((d) => d.account === a.id), a));
  const guests = devices.filter((d) => !d.account).map((d) => person([d], null));
  return { accounts, guests, ranks };
}

function DayBars({ series }) {
  const rows = (series || []).slice(-30);
  if (!rows.length) return html`<p class="whytxt">The daily count starts after the first UTC midnight.</p>`;
  const W = 600, H = 140, bw = W / 30;
  const max = Math.max(5, ...rows.map((r) => (r.d1?.ios || 0) + (r.d1?.android || 0)));
  const step = max > 40 ? 20 : max > 20 ? 10 : 5;
  const at = (i) => (30 - rows.length + i) * bw;
  return html`<svg viewBox=${`0 0 ${W} ${H + 18}`} class="rchart">
    ${Array.from({ length: Math.floor(max / step) }, (_, k) => (k + 1) * step).map((v) => html`<line x1="0" x2=${W} y1=${H - v / max * H} y2=${H - v / max * H} stroke="var(--line)"/><text x=${W} y=${H - v / max * H - 3} text-anchor="end">${v}</text>`)}
    ${rows.map((r, i) => { const hi = (r.d1?.ios || 0) / max * H, ha = (r.d1?.android || 0) / max * H;
      return html`<g><title>${ddmm(r.day * 86400)}: iPhone ${r.d1?.ios || 0}, Android ${r.d1?.android || 0}</title>
        <rect x=${at(i) + 2} y=${H - hi} width=${bw - 4} height=${hi} fill="var(--accent)" rx="1.5"/>
        ${ha ? html`<rect x=${at(i) + 2} y=${H - hi - ha} width=${bw - 4} height=${ha} fill="var(--home)" rx="1.5"/>` : null}</g>`; })}
    ${rows.map((r, i) => i % 7 === (rows.length - 1) % 7 ? html`<text x=${at(i) + bw / 2} y=${H + 15} text-anchor="middle">${ddmm(r.day * 86400)}</text>` : null)}
  </svg>`;
}

function WeekBars({ people, now }) {
  // Weeks from Monday (UTC), the last 8; signed in and guests.
  const monday = (t) => { const d = dayNo(t); return d - ((d + 3) % 7); };
  const thisWeek = monday(now);
  const weeks = Array.from({ length: 8 }, (_, k) => ({ start: thisWeek - (7 - k) * 7, acc: 0, guest: 0 }));
  for (const p of people) {
    if (!p.joined) continue;
    const w = weeks.find((x) => x.start === monday(p.joined));
    if (w) w[p.account ? "acc" : "guest"]++;
  }
  const W = 600, H = 140, ww = W / weeks.length, max = Math.max(5, ...weeks.map((w) => w.acc + w.guest));
  return html`<svg viewBox=${`0 0 ${W} ${H + 18}`} class="rchart">
    ${weeks.map((w, i) => { const ha = w.acc / max * H, hg = w.guest / max * H, x = i * ww + 10, bw = ww - 20, last = i === weeks.length - 1;
      return html`<g><rect x=${x} y=${H - ha} width=${bw} height=${ha} fill="var(--accent)" rx="2" opacity=${last ? 0.55 : 1}/>
        <rect x=${x} y=${H - ha - hg} width=${bw} height=${hg} fill="var(--line)" rx="2"/>
        ${w.acc + w.guest ? html`<text x=${x + bw / 2} y=${H - ha - hg - 5} text-anchor="middle" class="ink">${w.acc + w.guest}</text>` : null}
        <text x=${x + bw / 2} y=${H + 15} text-anchor="middle">${ddmm(w.start * 86400)}</text></g>`; })}
  </svg>`;
}

// Riders coming back (the owner, 01.11.2026: "make all", from the ops panel ideas): of the riders who joined in a week
// (from Monday, UTC), the share who opened the app again in each of the next four weeks. Days are known from 30.10.2026
// (device_days), so a week before that has no number; a week still running or cut by that date is paler.
const DAYS_FROM = 20756;
function ReturnTable({ people, now }) {
  const monday = (d) => d - ((d + 3) % 7);
  const today = dayNo(now), thisWeek = monday(today);
  const rows = Array.from({ length: 8 }, (_, k) => thisWeek - (7 - k) * 7).map((start) => {
    const cohort = people.filter((p) => p.joined && p.phones.length && monday(dayNo(p.joined)) === start);
    const cells = [1, 2, 3, 4].map((w) => {
      const a = start + 7 * w, b = a + 6;
      if (a > today) return null;
      if (b < DAYS_FROM) return { none: true };
      const back = cohort.filter((p) => p.dayList.some((d) => d >= a && d <= b)).length;
      return { back, share: cohort.length ? back / cohort.length : 0, partial: b > today || a < DAYS_FROM, running: b > today };
    });
    return { start, n: cohort.length, cells };
  }).filter((r) => r.n).reverse();
  if (!rows.length) return html`<p class="whytxt">Nobody joined in the last 8 weeks.</p>`;
  return html`<div class="tablewrap"><table class="ret"><thead><tr><th>Joined the week of</th><th class="r">Riders</th>${[1, 2, 3, 4].map((w) => html`<th class="r">Week ${w}</th>`)}</tr></thead><tbody>
    ${rows.map((r) => html`<tr><td>${ddmm(r.start * 86400)}</td><td class="r num">${r.n}</td>${r.cells.map((c) => !c ? html`<td></td>`
      : c.none ? html`<td class="r whytxt" title="days are known from 30.10.2026">–</td>`
      : html`<td class=${"r num" + (c.partial ? " part" : "")} style=${`background:color-mix(in srgb, var(--accent) ${Math.round(c.share * 55)}%, transparent)`}
          title=${`${c.back} of ${r.n}${c.partial ? (c.running ? ", the week is still running" : ", days known only from 30.10") : ""}`}>${Math.round(c.share * 100)} %</td>`)}</tr>`)}
  </tbody></table></div>
  <p class="whytxt" style="margin:8px 0 0">Week 1 is the week after joining. A rider counts as back when any of their phones opened the app that week. Paler: the week is still running, or days were only counted from 30.10.2026.</p>`;
}

const RIDER_COLUMNS = [
  ["name", "Rider", (p) => p.name.toLowerCase()], ["phone", "Phone", (p) => p.platform + p.phone], ["build", "Build", (p) => (p.platform === "ios" ? 1e6 : 0) + p.buildNo],
  ["country", "Country", (p) => p.country], ["week", "Last 7 days", (p) => p.week.filter(Boolean).length], ["days30", "Days in 30", (p) => p.days30, "r"], ["alerts", "Alerts", (p) => p.alerts, "r"],
  ["joined", "Joined", (p) => p.joined], ["last", "Last seen", (p) => p.last, "r"], ["role", "Role", (p) => p.role],
];

function RolesEditor({ roles, refresh, now }) {
  const [msg, setMsg] = useState("");
  const [sort, setSort] = useState({ key: "last", dir: -1 });
  const [f, setF] = useState({ who: "accounts", platform: "", build: "", role: "", active: "week", q: "" });
  const [open, setOpen] = useState(null);
  if (!roles) return html`<p class="empty">Reading riders and roles…</p>`;
  const post = async (body, what) => { const { data, status } = await adminPostRaw("roles", body); setMsg(data.error ? data.error : `✓ ${what}`); if (!data.error && status < 400) refresh(); };
  const { accounts, guests, ranks } = ridersOf(roles, now);
  const riders = [...accounts, ...guests.filter((p) => !p.test)];
  const within = (p, days) => now - p.last < days * 86400;
  const active7 = riders.filter((p) => within(p, 7) && p.phones.length);
  const latestShare = active7.length ? Math.round(100 * active7.filter((p) => p.buildKind === "latest").length / active7.length) : 0;
  const newWeek = riders.filter((p) => p.joined && now - p.joined < 7 * 86400).length;
  // Builds in use among this week's riders: latest / one behind / older, per platform.
  const builds = ["ios", "android"].map((pl) => {
    const list = active7.filter((p) => p.platform === pl);
    const n = (k) => list.filter((p) => p.buildKind === k).length;
    const older = list.filter((p) => p.buildKind === "older").map((p) => p.buildNo).filter(Boolean);
    const pre = pl === "android" ? "A" : "";
    return { pl, total: list.length, latest: n("latest"), behind: n("behind"), older: n("older"), top: ranks[pl][0], olderRange: older.length ? pre + Math.min(...older) + (Math.max(...older) > Math.min(...older) ? "–" + pre + Math.max(...older) : "") : "" };
  }).filter((b) => b.total);
  const countries = Object.entries(riders.filter((p) => within(p, 30) && p.country).reduce((o, p) => (o[p.country] = (o[p.country] || 0) + 1, o), {}))
    .sort((a, b) => b[1] - a[1]).slice(0, 6);
  const cMax = Math.max(1, ...countries.map((c) => c[1]));
  // The table.
  const q = f.q.trim().toLowerCase();
  const pool = f.who === "accounts" ? accounts : guests;
  const shown = pool.filter((p) => (!f.platform || p.platform === f.platform) && (!f.build || (f.build === "latest" ? p.buildKind === "latest" : p.buildKind && p.buildKind !== "latest"))
    && (!f.role || p.role === f.role) && (f.active === "any" || within(p, { today: 1, week: 7, month: 30 }[f.active]))
    && (!q || [p.name, p.mail, p.phone, p.os, p.build, p.country, ...p.phones.map((d) => d.device)].join(" ").toLowerCase().includes(q)));
  const col = RIDER_COLUMNS.find((c) => c[0] === sort.key) || RIDER_COLUMNS.find((c) => c[0] === "last");
  shown.sort((a, b) => { const x = col[2](a), y = col[2](b); return (x < y ? -1 : x > y ? 1 : 0) * sort.dir || b.last - a.last; });
  const chip = (key, value, label) => html`<button class=${"chip" + (f[key] === value ? " on" : "")} onClick=${() => setF({ ...f, [key]: value })}>${label}</button>`;
  const head = (c) => html`<th class=${(c[3] || "") + (sort.key === c[0] ? " sorted" : "") + " sortable"} onClick=${() => setSort({ key: c[0], dir: sort.key === c[0] ? -sort.dir : (c[0] === "name" || c[0] === "country" ? 1 : -1) })}>
    ${c[1]}${sort.key === c[0] ? html` <span class="ar">${sort.dir < 0 ? "▾" : "▴"}</span>` : null}</th>`;
  const roleSelect = (p) => p.account
    ? html`<select value=${p.role} onChange=${(e) => post({ action: "accountRole", account: p.account.id, role: e.target.value }, p.name + " is " + e.target.value)}>${ROLES.map((r) => html`<option value=${r}>${r}</option>`)}</select>`
    : html`<select value=${p.role} onChange=${(e) => post({ action: "role", device: p.main.device, role: e.target.value }, "phone is " + e.target.value)}>${ROLES.map((r) => html`<option value=${r}>${r}</option>`)}</select>`;
  const a = (p) => p.account;
  return html`<div class="grid">
    <div class="panel s12"><div class="rtiles">
      <div class="ftile"><div class="v num">${riders.filter((p) => within(p, 1)).length}</div><div class="l">active today</div></div>
      <div class="ftile"><div class="v num">${active7.length}</div><div class="l">active this week</div></div>
      <div class="ftile"><div class="v num">${riders.filter((p) => within(p, 30)).length}</div><div class="l">active in 30 days</div></div>
      <div class="ftile"><div class="v num">${newWeek}</div><div class="l">new this week</div></div>
      <div class="ftile"><div class="v num">${latestShare} %</div><div class="l">on the latest build</div></div>
    </div></div>
    <div class="panel s6"><h2>Active riders a day · 30 days</h2><${DayBars} series=${roles.riders && roles.riders.series} />
      <div class="legend"><span><i class="sw" style="background:var(--accent)"></i>iPhone</span><span><i class="sw" style="background:var(--home)"></i>Android</span><span>phones that opened the app that UTC day</span></div></div>
    <div class="panel s6"><h2>New riders a week</h2><${WeekBars} people=${riders} now=${now} />
      <div class="legend"><span><i class="sw" style="background:var(--accent)"></i>with an account</span><span><i class="sw" style="background:var(--line)"></i>guests</span><span>this week so far, paler</span></div></div>
    <div class="panel s6"><h2>Builds in use · active this week</h2>
      ${builds.map((b) => html`<div class="rbar"><span>${b.pl === "ios" ? "iPhone" : "Android"}</span><div class="hb">
        ${[["latest", "var(--ok)"], ["behind", "var(--accent-soft)"], ["older", "var(--warn)"]].map(([k, c]) => b[k] ? html`<i style=${`width:${100 * b[k] / b.total}%;background:${c}`} title=${`${b[k]} ${k}`}
          onClick=${() => setF({ ...f, who: "accounts", platform: b.pl, build: k === "latest" ? "latest" : "behind", active: "week" })}></i>` : null)}</div><span class="num n">${b.total}</span></div>`)}
      <div class="legend"><span><i class="sw" style="background:var(--ok)"></i>latest (${builds.map((b) => (b.pl === "ios" ? "iPhone " : "Android A") + b.top).join(", ")})</span>
        <span><i class="sw" style="background:var(--accent-soft)"></i>one behind</span>
        ${builds.some((b) => b.older) ? html`<span><i class="sw" style="background:var(--warn)"></i>older: ${builds.filter((b) => b.older).map((b) => `${b.older} on ${b.olderRange}`).join(", ")}</span>` : null}</div>
      <p class="whytxt" style="margin:8px 0 0">Click a bar to list those riders below.</p></div>
    <div class="panel s6"><h2>Countries · active in 30 days</h2>
      ${countries.map(([c, n]) => html`<div class="rbar"><span><span class="flagcode">${c}</span></span><div class="hb"><i style=${`width:${100 * n / cMax}%;background:var(--accent)`}></i></div><span class="num n">${n}</span></div>`)}
      ${!countries.length ? html`<p class="whytxt">No countries yet.</p>` : null}</div>
    <div class="panel s12"><h2>Riders coming back · by the week they joined</h2><${ReturnTable} people=${riders} now=${now} /></div>
    <div class="panel s12">
      <h2>Riders · ${accounts.length} accounts · ${guests.filter((p) => !p.test).length} guests${guests.some((p) => p.test) ? ` · ${guests.filter((p) => p.test).length} test phones` : ""}</h2>
      <div class="rfilters">
        <input placeholder="Find a name, email, phone, build…" value=${f.q} onInput=${(e) => setF({ ...f, q: e.target.value })}/>
        <span class="grp"><span class="lab">Phone</span>${chip("platform", "", "All")}${chip("platform", "ios", "iPhone")}${chip("platform", "android", "Android")}</span>
        <span class="grp"><span class="lab">Build</span>${chip("build", "", "All")}${chip("build", "latest", "Latest")}${chip("build", "behind", "Behind")}</span>
        <span class="grp"><span class="lab">Role</span>${chip("role", "", "All")}${ROLES.map((r) => chip("role", r, r[0].toUpperCase() + r.slice(1)))}</span>
        <span class="grp"><span class="lab">Active</span>${chip("active", "today", "Today")}${chip("active", "week", "This week")}${chip("active", "month", "30 days")}${chip("active", "any", "Any")}</span>
        <span class="grp">${chip("who", "accounts", "Accounts")}${chip("who", "guests", "Guests")}</span>
        <span class="grp"><b class="s13">New riders get</b> <select value=${roles.defaultRole} onChange=${(e) => post({ action: "default", role: e.target.value }, "default role " + e.target.value)}>${ROLES.map((r) => html`<option value=${r}>${r}</option>`)}</select></span>
        <${Saved} msg=${msg} />
      </div>
      <div class="tablewrap"><table class="riders"><thead><tr>${RIDER_COLUMNS.map(head)}<th></th></tr></thead><tbody>
        ${shown.map((p) => html`<tr class=${open === p.key ? "open" : ""} onClick=${(e) => { if (!/SELECT|BUTTON|OPTION|A/.test(e.target.tagName)) setOpen(open === p.key ? null : p.key); }}>
          <td><div class="who">${a(p) && a(p).photo && !a(p).photo_blocked ? html`<img src=${a(p).photo} alt="" class="avatar"/>` : html`<span class="avatar ph">${(p.name.replace("@", "")[0] || "?").toUpperCase()}</span>`}
            <div><b>${p.name}</b>${p.test ? html` <span class="tag">${p.test}</span>` : null}<span class="sub">${p.mail || (p.account ? "" : "no account")}</span></div></div></td>
          <td><span class="plat">${PLATFORM_ICON[p.platform]}${p.phone}${p.phones.length > 1 ? html` <span class="whytxt">+${p.phones.length - 1}</span>` : null}</span><span class="sub">${p.os || "OS: older build"}</span></td>
          <td><span class=${"bld " + p.buildKind}>${p.platform === "android" && p.build ? "A" : ""}${p.build || "–"}</span><span class="sub">${{ latest: "latest", behind: "one behind", older: "older" }[p.buildKind] || ""}</span></td>
          <td class="hide-s">${p.country ? html`<span class="flagcode">${p.country}</span>` : "–"}</td>
          <td><span class="week" title="the last 7 UTC days, today on the right">${p.week.map((on) => html`<i class=${on ? "on" : ""}></i>`)}</span></td>
          <td class="r num hide-s">${p.days30}</td>
          <td class="r num hide-s" title="rain and wind alerts on the rider's phones">${p.alerts || "–"}</td>
          <td class="hide-s">${p.joined ? ddmm(p.joined) : "–"}</td>
          <td class="r num">${p.last ? agoShort(now - p.last) : "–"}</td>
          <td>${roleSelect(p)}</td><td class="hide-s"><span class="more">${open === p.key ? "▴" : "▾"}</span></td></tr>
          ${open === p.key ? html`<tr class="rdetail"><td colspan="11"><div class="dgrid">
            <div><h4>Phones · ${p.phones.length}</h4>${p.phones.map((d) => html`<div>${phoneName(d)}${d.os ? " · " + d.os : ""} · <span class="bld">${platformOf(d) === "android" && buildNo(d) ? "A" + buildNo(d) : d.build || "?"}</span> · ${d.country || "?"} · ${agoShort(now - d.last_seen)}
              <span class="whytxt mono">${String(d.device).slice(0, 8)}</span></div>`)}${!p.phones.length ? html`<div class="whytxt">none yet</div>` : null}</div>
            <div><h4>${p.account ? "Signed in with" : "Account"}</h4>${p.account ? html`${[a(p).apple ? "Apple" : null, a(p).google ? "Google" : null].filter(Boolean).join(", ") || "email code"}
              ${a(p).created ? ` · since ${ddmm(a(p).created)}` : ""}<span class="sub">${[a(p).first, a(p).last].filter(Boolean).join(" ") || ""}${a(p).first || a(p).last ? " (team only)" : ""}</span>` : html`<span class="whytxt">none: a guest on this phone</span>`}</div>
            <div><h4>Spots</h4>shared ${p.shared} · days active in 30: ${p.days30}<span class="sub">first-launch sets: ${p.sets.length ? p.sets.join(", ") : "not known (only riders who installed on build 396 / A19 or later)"}</span></div>
            <div><h4>Photo</h4>${p.account && a(p).photo ? html`<span class="btns"><button class="btn" onClick=${() => post({ action: "photo", account: a(p).id, remove: true }, "photo removed")}>Remove photo</button>
              <button class="btn danger" onClick=${() => post({ action: "photo", account: a(p).id, block: !a(p).photo_blocked }, a(p).photo_blocked ? "photos allowed" : "photos blocked")}>${a(p).photo_blocked ? "Allow photos" : "Block photos"}</button></span>` : html`<span class="whytxt">no photo</span>`}</div>
          </div></td></tr>` : null}`)}
        ${!shown.length ? html`<tr><td colspan="11" class="whytxt">Nobody matches these filters.</td></tr>` : null}
      </tbody></table></div>
      <p class="whytxt" style="margin:10px 0 0">Showing ${shown.length} of ${pool.length}. Click a column to sort, a row for its phones. Joined: the first time any of the rider's phones opened the app, or the account's start.
        Last 7 days and Days in 30 count from 30.10.2026 (one mark a day a phone opens the app); the OS comes from build 396 and A19 on, the first-launch sets only from riders who installed one of those. Alerts: rain and wind alerts set on the rider's phones. Test phones (emulator, Simulator, Apple's review) are left out of the numbers and charts.</p>
    </div>
  </div>`;
}

function FeatureRow({ x, post }) {
  const [release, setRelease] = useState(x.release);
  const [roles, setRoles] = useState(new Set(x.roles || []));
  const [account, setAccount] = useState(!!x.account);
  const changed = release !== x.release || account !== !!x.account || [...roles].sort().join() !== [...(x.roles || [])].sort().join();
  return html`<tr><td><b>${x.name}</b><div class="whytxt">${x.key}${x.soon ? " · " + x.soon : ""}</div></td><td class="whytxt">${x.grp}</td>
    <td><select value=${release} onChange=${(e) => setRelease(e.target.value)}><option value="released">released</option><option value="testers">testers only</option><option value="off">off</option></select></td>
    <td><span class="btns">${ROLES.map((r) => html`<label class="row"><input type="checkbox" checked=${roles.has(r)} disabled=${r === "admin"} onChange=${() => { const n = new Set(roles); n.has(r) ? n.delete(r) : n.add(r); setRoles(n); }}/> ${r}</label>`)}</span></td>
    <td><label class="row"><input type="checkbox" checked=${account} onChange=${(e) => setAccount(e.target.checked)}/> signed in only</label></td>
    <td class="r">${changed ? html`<button class="btn primary" onClick=${() => post({ action: "feature", key: x.key, release, roles: [...roles], account }, x.name)}>Save</button>` : null}</td></tr>`;
}

function FeaturesEditor({ roles, refresh }) {
  const [msg, setMsg] = useState("");
  if (!roles) return html`<p class="empty">Reading features…</p>`;
  const post = async (body, what) => { const { data } = await adminPostRaw("roles", body); setMsg(data.error ? data.error : `✓ ${what} saved`); if (!data.error) refresh(); };
  return html`<div class="panel"><h2>Features · who gets what</h2><p class="note">Admins always get everything. "Testers only": only the tester role. "Released": the roles ticked.</p>
    <div class="tablewrap"><table><thead><tr><th>Feature</th><th>Group</th><th>Release</th><th>Roles</th><th>Accounts</th><th></th></tr></thead>
      <tbody>${(roles.features || []).map((x) => html`<${FeatureRow} key=${x.key + x.release + (x.roles || []).join()} x=${x} post=${post} />`)}</tbody></table></div>
    <${Saved} msg=${msg} /></div>`;
}

function StationAdd({ st, refresh }) {
  const kinds = (st && st.kinds) || {};
  const [q, setQ] = useState({ source: Object.keys(kinds)[0] || "", lat: "", lon: "", km: "30" });
  const [found, setFound] = useState(null);
  const [msg, setMsg] = useState("");
  const [show, setShow] = useState(true);
  const find = async () => {
    setMsg("…");
    try { const d = await adminGet(`stations/find?source=${encodeURIComponent(q.source)}&lat=${encodeURIComponent(q.lat)}&lon=${encodeURIComponent(q.lon)}&km=${encodeURIComponent(q.km)}`);
      if (d.error) throw new Error(d.error); setFound(d); setMsg(""); } catch (e) { setMsg(String(e.message || e)); }
  };
  const add = async (x) => {
    const { data } = await adminPostRaw("stations", { action: "add", source: q.source, code: x.code, name: x.name, lat: x.lat, lon: x.lon, show });
    setMsg(data.error ? data.error : `✓ added ${data.station ? data.station.name : x.name}${show ? "" : " (hidden)"}`);
    if (!data.error) { refresh(); find(); }
  };
  return html`<div class="panel s12"><h2>Find and add a station</h2><div class="form four">
      <label>Source<select value=${q.source} onChange=${(e) => setQ({ ...q, source: e.target.value })}>${Object.entries(kinds).map(([k, v]) => html`<option value=${k}>${v.name}</option>`)}</select></label>
      <label>Latitude<input value=${q.lat} onInput=${(e) => setQ({ ...q, lat: e.target.value })} placeholder="36.01"/></label>
      <label>Longitude<input value=${q.lon} onInput=${(e) => setQ({ ...q, lon: e.target.value })} placeholder="-5.60"/></label>
      <label>Within km<input value=${q.km} onInput=${(e) => setQ({ ...q, km: e.target.value })}/></label></div>
    <div class="btns" style="margin-top:8px;align-items:center"><button class="btn primary" onClick=${find} disabled=${!q.lat || !q.lon}>Find stations</button>
      <label class="row"><input type="checkbox" checked=${show} onChange=${(e) => setShow(e.target.checked)}/> show on the map when added</label><${Saved} msg=${msg} /></div>
    ${found ? html`<div class="tablewrap" style="margin-top:10px"><table><thead><tr><th>Station</th><th class="r">Distance</th><th class="r">Wind</th><th class="r">Reading</th><th></th></tr></thead><tbody>
      ${(found.found || []).map((x) => html`<tr><td>${x.name}<div class="whytxt">${x.code}</div></td><td class="r num">${x.km.toFixed(1)} km</td><td class="r num">${x.wind != null ? x.wind + " m/s" : "–"}</td>
        <td class="r num">${x.time ? hrs(Date.now() / 1000 - x.time) + " ago" : "–"}</td>
        <td class="r">${x.added ? html`<span class="st ok">added</span>` : html`<button class="btn" onClick=${() => add(x)}>Add</button>`}</td></tr>`)}</tbody></table>
      <p class="whytxt">${found.listed} stations listed for this source, list updated ${found.updated ? hrs(Date.now() / 1000 - found.updated) + " ago" : "–"}.</p></div>` : null}
  </div>`;
}

function StationRow({ x, refresh, now }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: x.name, note: x.note || "", windguru: x.windguru || "", twinKm: x.twinKm ?? "", lat: x.lat, lon: x.lon });
  const [msg, setMsg] = useState("");
  const [far, setFar] = useState(null);
  const save = async (confirmFar) => {
    const body = { action: "edit", id: x.id, name: f.name, note: f.note, windguru: f.windguru === "" ? null : f.windguru, twinKm: f.twinKm === "" ? undefined : Number(f.twinKm) };
    if (String(f.lat) !== String(x.lat) || String(f.lon) !== String(x.lon)) { body.lat = Number(f.lat); body.lon = Number(f.lon); if (confirmFar) body.far = true; }
    const { status, data } = await adminPostRaw("stations", body);
    if (status === 409 && data.far) { setFar(data.error); return; }
    setMsg(data.error ? data.error : "✓ saved"); setFar(null);
    if (!data.error) refresh();
  };
  const toggle = async () => { const { data } = await adminPostRaw("stations", { action: x.hidden ? "show" : "hide", id: x.id }); setMsg(data.error ? data.error : "✓"); refresh(); };
  return html`<tr><td>${x.name}${x.hidden ? html` <span class="st unknown">hidden</span>` : null}<div class="whytxt">${x.id}</div></td><td class="whytxt">${x.source}</td>
    <td class="r num" style=${x.time && now - x.time > 1200 ? "color:var(--warn)" : ""}>${x.time ? hrs(now - x.time) + " ago" : "–"}</td>
    <td class="r num">${x.wind != null ? x.wind + (x.gust != null ? "/" + x.gust : "") + " m/s" : "–"}</td>
    <td class="r"><span class="btns"><button class="btn" onClick=${() => setOpen(!open)}>${open ? "Close" : "Edit"}</button><button class="btn" onClick=${toggle}>${x.hidden ? "Show" : "Hide"}</button></span></td></tr>
    ${open ? html`<tr><td colspan="5"><div class="form four">
      <label>Name<input value=${f.name} onInput=${(e) => setF({ ...f, name: e.target.value })}/></label>
      <label>Note<input maxlength="300" value=${f.note} onInput=${(e) => setF({ ...f, note: e.target.value })}/></label>
      <label>Windguru station id (internal)<input value=${f.windguru} onInput=${(e) => setF({ ...f, windguru: e.target.value })}/></label>
      <label>Twin km (0-10)<input value=${f.twinKm} onInput=${(e) => setF({ ...f, twinKm: e.target.value })}/></label>
      <label>Latitude<input value=${f.lat} onInput=${(e) => setF({ ...f, lat: e.target.value })}/></label>
      <label>Longitude<input value=${f.lon} onInput=${(e) => setF({ ...f, lon: e.target.value })}/></label></div>
      <div class="btns" style="margin-top:8px;align-items:center"><button class="btn primary" onClick=${() => save(false)}>Save</button><${Saved} msg=${msg} />
        ${far ? html`<span class="confirm">${far} <button class="btn danger" onClick=${() => save(true)}>Move it that far</button></span>` : null}</div></td></tr>` : null}`;
}

function StationsEditor({ st, refresh, now }) {
  if (!st) return null;
  return html`<div class="grid" style="margin-top:16px"><${StationAdd} st=${st} refresh=${refresh} />
    <div class="panel s12"><h2>Edit stations · ${(st.stations || []).length}</h2><div class="tablewrap"><table><thead><tr><th>Station</th><th>Source</th><th class="r">Last reading</th><th class="r">Wind</th><th></th></tr></thead><tbody>
      ${(st.stations || []).slice().sort((a, b) => a.name.localeCompare(b.name)).map((x) => html`<${StationRow} key=${x.id} x=${x} refresh=${refresh} now=${now} />`)}</tbody></table></div></div></div>`;
}

