// Feedback: riders' reports from the apps' feedback button, moved from the full admin (the owner, 30.10.2026: "both
// have some info what others dont have... We have to organize everything better"; ops as the one panel). The same
// admin routes: GET /api/feedback?status=, POST /api/feedback {id, status | note}, the files at /api/feedback/file.
const FB_TOPICS = { bug: "Bug", idea: "Idea", forecast: "Forecast was off", other: "Other" };

function FeedbackCard({ r, post }) {
  const c = r.context || {};
  const [note, setNote] = useState(r.note || "");
  const who = r.account ? (r.nickname || r.first || "rider") : "guest";
  const when = new Date(r.time * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const facts = [c.platform === "android" ? "Android" : "iPhone", r.build && "build " + r.build, r.model && phoneName({ model: r.model }), r.os, c.screen,
    c.spotName || c.place, c.layer && "layer " + c.layer, c.theme, c.lang, c.country].filter(Boolean).join(" · ");
  const s = c.showed || {};
  const subject = encodeURIComponent("Your MeteoGust feedback: " + (FB_TOPICS[r.topic] || "feedback"));
  const quote = encodeURIComponent("\n\n> " + String(r.text || "").split("\n").join("\n> "));
  return html`<div class="panel s12 fbcard">
    <div class="fbhead"><span class=${"pill " + (r.topic === "bug" ? "bad" : r.topic === "forecast" ? "warn" : "okp")}>${FB_TOPICS[r.topic] || r.topic}</span>
      <b>${who}</b> <span class="whytxt">#${r.id} · ${when}</span></div>
    <div class="whytxt" style="margin-top:4px">${facts}</div>
    ${r.topic === "forecast" ? html`<div class="fbfc"><b>${c.hour ? new Date(c.hour * 1000).toLocaleString([], { weekday: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "?"}</b>:
      we showed ${s.wind ?? "?"} gust ${s.gust ?? "?"} ${s.dir ?? ""}${s.unit ? " " + s.unit : ""}
      ${s.models ? html`<div class="whytxt" style="margin-top:2px">${Object.entries(s.models).map(([m, v]) => m + " " + v).join(", ")}</div>` : null}
      ${c.observed && c.observed.length ? html`<div>Rider: ${c.observed.map((o) => html`<span class="tag">${o}</span> `)}</div>` : null}</div>` : null}
    ${r.text ? html`<div class="fbtext">${r.text}</div>` : null}
    ${(r.files || []).length ? html`<div class="fbfiles">${r.files.map((key) => { const src = "/admin/api/feedback/file?key=" + encodeURIComponent(key);
      return key.includes(".mp4.") ? html`<video controls preload="metadata" src=${src}></video>` : html`<a href=${src} target="_blank"><img loading="lazy" src=${src} alt=""/></a>`; })}</div>` : null}
    <div class="btns fbact">${["new", "answered", "done"].map((st) => html`<button class=${"chip" + (r.status === st ? " on" : "")} onClick=${() => post({ id: r.id, status: st }, true)}>${st[0].toUpperCase() + st.slice(1)}</button>`)}
      ${r.email ? html`<a class="btn" href=${"mailto:" + r.email + "?subject=" + subject + "&body=" + quote}>Answer by email</a>` : null}
      <input class="fbnote" placeholder="Note (only here)" value=${note} onInput=${(e) => setNote(e.target.value)} onChange=${() => post({ id: r.id, note }, false)}/></div>
  </div>`;
}

function FeedbackList({ onCount }) {
  const [filter, setFilter] = useState("new");
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const load = async (f = filter) => {
    try {
      const d = await adminGet("feedback" + (f ? "?status=" + f : ""));
      if (d.error) throw new Error(d.error);
      setData(d); setErr(""); if (onCount) onCount((d.counts || {}).new || 0);
    } catch (e) { setErr(String(e.message || e)); }
  };
  useEffect(() => { load(filter); }, [filter]);
  const post = async (body, reload) => { const { data: d } = await adminPostRaw("feedback", body); if (d.error) alertMsg(d.error); else if (reload) load(); };
  const n = (data && data.counts) || {};
  return html`<div class="grid">
    <div class="panel s12"><h2>Feedback · ${n.new || 0} new, ${n.answered || 0} answered, ${n.done || 0} done</h2>
      <div class="btns">${[["new", "New"], ["answered", "Answered"], ["done", "Done"], ["", "All"]].map(([k, label]) => html`<button class=${"chip" + (filter === k ? " on" : "")} onClick=${() => setFilter(k)}>${label}</button>`)}</div>
      <p class="whytxt" style="margin:8px 0 0">Riders' reports from the app's feedback button. Mark one Answered after writing back, Done when it's dealt with; the note stays here.</p></div>
    ${err ? html`<p class="empty s12">Can't load: ${err}</p>` : !data ? html`<p class="empty s12">Reading the feedback…</p>`
      : !data.reports.length ? html`<p class="empty s12">Nothing here.</p>` : data.reports.map((r) => html`<${FeedbackCard} key=${r.id + ":" + r.status} r=${r} post=${post} />`)}
  </div>`;
}

