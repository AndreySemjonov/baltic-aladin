const APP_TABS = [["overview", "Overview"], ["spots", "Spots"], ["points", "Weight points"], ["roles", "Riders & roles"], ["feedback", "Feedback"], ["releases", "Releases"], ["features", "Features"]];

function AppSection(props) {
  const [tab, setTab] = useState("overview");
  // New feedback on the tab's chip, read once when the section opens (the list itself re-reads it).
  const [fbNew, setFbNew] = useState(0);
  useEffect(() => { adminGet("feedback?status=new").then((d) => setFbNew((d.counts || {}).new || 0)).catch(() => {}); }, []);
  return html`<div class="subtabs">${APP_TABS.map(([k, label]) => html`<button class=${"chip" + (tab === k ? " on" : "")} onClick=${() => setTab(k)}>${label}${k === "feedback" && fbNew ? html` <b style="color:var(--bad)">${fbNew}</b>` : null}</button>`)}</div>
    ${tab === "overview" ? html`<${AppRiders} ...${props} goTab=${setTab} />` : tab === "spots" ? html`<${SpotsEditor} spots=${props.spots} roles=${props.roles} refresh=${props.reloadSpots} now=${props.now} />`
      : tab === "points" ? html`<${WeightPoints} spots=${props.spots} refresh=${props.reloadSpots} />`
      : tab === "roles" ? html`<${RolesEditor} roles=${props.roles} refresh=${props.reloadRoles} now=${props.now} />`
      : tab === "feedback" ? html`<${FeedbackList} onCount=${setFbNew} />`
      : tab === "releases" ? html`<${Releases} roles=${props.roles} builds=${props.builds} ad=${props.ad} now=${props.now} />`
      : html`<${FeaturesEditor} roles=${props.roles} refresh=${props.reloadRoles} />`}`;
}

function App() {
  const [f, setF] = useState(null);
  const [s, setS] = useState(null);
  const [fr, setFr] = useState(null);
  const [cap, setCap] = useState(null);
  const [ad, setAd] = useState(null);
  const [maps, setMaps] = useState(null);
  const [spots, setSpots] = useState(null);
  const [roles, setRoles] = useState(null);
  const [builds, setBuilds] = useState(null);
  const [st, setSt] = useState(null);
  const pick = () => { const h = location.hash.slice(1); return SECTIONS.some(([k]) => k === h) ? h : "pipeline"; };
  const [sec, setSec] = useState(pick());
  const [zone, setZoneState] = useState(ZONE);
  const [, tick] = useState(0);
  const setZone = (z) => { ZONE = z; try { localStorage.setItem("ops-zone", z); } catch (e) {} setZoneState(z); };
  const load = async (list) => { for (const [k, fn] of list) { try { fn(await adminGet(k)); } catch (e) {} } };
  const refresh = () => load([["spots", setSpots]]);
  const reloadRoles = () => load([["roles", setRoles]]);
  const reloadStations = () => load([["stations", setSt]]);
  useEffect(() => {
    let last = Date.now();
    const es = new EventSource("/api/stream");
    es.addEventListener("fast", (e) => { last = Date.now(); setF(JSON.parse(e.data)); });
    es.addEventListener("slow", (e) => setS(JSON.parse(e.data)));
    es.addEventListener("fresh", (e) => setFr(JSON.parse(e.data)));
    es.addEventListener("capacity", (e) => setCap(JSON.parse(e.data)));
    es.addEventListener("admin", (e) => setAd(JSON.parse(e.data)));
    const onHash = () => setSec(pick());
    addEventListener("hashchange", onHash);
    // Admin's own data: the maps' history every 10 minutes (it reads ~3,000 rows), the rest every 5.
    load([["maps", setMaps], ["spots", setSpots], ["roles", setRoles], ["builds", setBuilds], ["stations", setSt]]);
    const slowAdmin = setInterval(() => load([["maps", setMaps]]), 600000);
    const midAdmin = setInterval(() => load([["spots", setSpots], ["builds", setBuilds], ["stations", setSt], ["roles", setRoles]]), 300000);
    // Without the stream (a proxy holding it back), ask every 3 seconds instead.
    const poll = setInterval(async () => {
      if (Date.now() - last < 8000) return;
      try {
        setF(await (await fetch("/api/fast")).json());
        if (Date.now() % 30000 < 3000) {
          setS(await (await fetch("/api/slow")).json());
          setFr(await (await fetch("/api/fresh")).json());
          setCap(await (await fetch("/api/capacity")).json());
          setAd(await (await fetch("/api/admin")).json());
        }
      } catch (err) {}
    }, 3000);
    const clockTimer = setInterval(() => tick((n) => n + 1), 1000);
    return () => { es.close(); clearInterval(poll); clearInterval(clockTimer); clearInterval(slowAdmin); clearInterval(midAdmin); removeEventListener("hashchange", onHash); };
  }, []);
  const now = Date.now() / 1000;
  if (!f) return html`<main class="wrap"><p class="empty">Connecting to the Tower…</p></main>`;
  return html`<${TopBar} f=${f} fr=${fr} ad=${ad} spots=${spots} zone=${zone} setZone=${setZone} sec=${sec} />
    <main class=${"wrap" + (sec === "pipeline" ? " wide" : "")}>
      ${sec === "pipeline" ? html`<${Pipeline} maps=${maps} fr=${fr} f=${f} now=${now} s=${s} cap=${cap} ad=${ad} />`
        : sec === "machine" ? html`<${NowTab} f=${f} s=${s} now=${now} /><div style="margin-top:16px"><${Capacity} cap=${cap} now=${now} /></div>`
        : sec === "quality" ? html`<${Quality} ad=${ad} now=${now} />`
        : sec === "rain" ? html`<${RainClouds} ad=${ad} now=${now} />`
        : sec === "app" ? html`<${AppSection} ad=${ad} spots=${spots} roles=${roles} builds=${builds} refresh=${refresh} reloadSpots=${refresh} reloadRoles=${reloadRoles} now=${now} />`
        : sec === "stations" ? html`<${StationsSec} ad=${ad} st=${st} now=${now} /><${StationsEditor} st=${st} refresh=${reloadStations} now=${now} />`
        : html`<${Costs} ad=${ad} now=${now} />`}
    <div class="foot"><span>Live every 2 seconds; admin's data every 5-10 minutes.</span><span>Changes go through admin's own routes and ask once more.</span><a href="/admin/" style="color:var(--accent)">Full admin</a></div></main>`;
}

