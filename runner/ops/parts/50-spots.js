// MARK: Native editors (the owner, 21.10.2026: "native screens for the deep editors... make it"): spots and their
// info, weights and photos; weight points; riders' and devices' roles; features; finding and adding stations.
// Every change goes through admin's own routes (the contracts of tools/admin/worker.js).

const WEIGHT_MODELS = [["dmi_harmonie_arome_europe", "HARM-DK 2 km"], ["metno_nordic", "MET Nordic 1 km"], ["aladin", "ALADIN 2.3 km"], ["fmi", "HARM-FI 2.5 km"],
  ["meteofrance_arome_france_hd", "AROME-FR 1.5 km"], ["knmi_harmonie_arome_netherlands", "HARM-NL 2 km"], ["ukmo_uk_deterministic_2km", "UKV 2 km"],
  ["icon_d2", "ICON-D2 2.2 km"], ["meteoswiss_icon_ch1", "ICON-CH1 1 km"], ["meteoswiss_icon_ch2", "ICON-CH2 2 km"], ["italia_meteo_arpae_icon_2i", "ICON-2I 2.2 km"],
  ["meteofrance_arome_france", "AROME-FR 2.5 km"], ["ncep_hrrr_conus", "HRRR 3 km"], ["gem_hrdps_continental", "HRDPS 2.5 km"],
  ["knmi_harmonie_arome_europe", "HARMONIE 5 km"], ["icon_eu", "ICON 7 km"], ["ecmwf_ifs", "ECMWF 9 km"], ["gfs_global", "GFS 13 km"],
  ["icon_global", "ICON 13 km"], ["gem_global", "GDPS 15 km"], ["wrf_tarifa", "WRF 1 km (ours, Tarifa)"]];
const ROLES = ["basic", "plus", "tester", "admin"];

// A POST whose answer is shown as it came (admin answers some refusals with HTTP 200 and {error}, a far move with
// {far:true}): {status, data}.
async function adminPostRaw(path, body) {
  const r = await fetch("/admin/api/" + path, { method: "POST", headers: { "content-type": "application/json", "x-shorewind-admin": "1" }, body: JSON.stringify(body) });
  let data = {};
  try { data = await r.json(); } catch (e) { data = { error: `HTTP ${r.status}` }; }
  return { status: r.status, data };
}

function Saved({ msg }) {
  if (!msg) return null;
  return html`<span class="whytxt" style=${msg.startsWith("✓") ? "color:var(--ok)" : "color:var(--bad)"}>${msg}</span>`;
}

// One box per model, in %; only the ratios count (admin makes them add up to 1).
function WeightsGrid({ value, onChange }) {
  const v = value || {};
  return html`<div class="wgrid">${WEIGHT_MODELS.map(([k, label]) => html`<label><span>${label}</span>
    <input inputmode="decimal" value=${v[k] ? String(Math.round(v[k] * 1000) / 10) : ""} placeholder="–"
      onChange=${(e) => { const n = parseFloat(String(e.target.value).replace(",", ".")); const next = { ...v }; if (n > 0) next[k] = n / 100; else delete next[k]; onChange(next); }}/></label>`)}</div>`;
}

// The spot editor and the riders' suggestions: admin.meteogust.com's own page code (tools/admin/worker.js in the app's
// repo), ported whole so nothing is lost on the way (the owner, 23.10.2026: "spots missing a lot of from original
// admin.meteogust.com. For example now wind direction edit, map etc."): the wind rose, the map with pins and zones and
// OpenStreetMap's amenities, photos and videos (the in-browser converter), rules, water level, best months from
// history, weights, moving the point, draft and publish. Its own scope, as its names (MONTHS, WEIGHT_MODELS...) are
// the panel's too; its calls go to /admin/api/.
const OldSpots = (() => {
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
// A pending public spot's description from its rider (approved with the spot, it becomes its info).
function described(r) {
  const x = r.described;
  if (!x) return "";
  const list = (label, v) => (v && v.length ? "<div><b>" + label + "</b> " + esc(v.join(", ")) + "</div>" : "");
  return '<div class="sugg" style="margin:-4px 0 12px 12px">' +
    (x.note ? '<div class="snote">“' + esc(x.note) + "”</div>" : "") + (x.info ? "" : list("Level", x.levels) + list("Water", x.water) + list("Hazards", x.hazards)) +
    (x.info ? Object.keys(SUGG_FIELDS).filter((k) => k !== "about" && x.info[k] != null).map((k) => "<div><b>" + SUGG_FIELDS[k] + "</b> " + esc(suggLabel(k, x.info[k])) + "</div>").join("") :
      list("Amenities", x.amenities) + (x.crowds ? "<div><b>Crowds</b> " + esc(x.crowds) + "</div>" : "") + (x.rule ? "<div><b>Rule</b> " + esc(x.rule) + "</div>" : "")) +
    (x.photos && x.photos.length ? '<div class="sphs">' + x.photos.map((u) => '<a target="_blank" href="' + esc(u) + '"><img src="' + esc(u) + '" style="width:120px;height:80px;object-fit:cover;border-radius:6px"></a>').join("") + "</div>" : "") +
    '<div class="note" style="margin:6px 0 0">Approve: the description becomes the spot page (photos credited to ' + esc(x.by || r.author || "the rider") + "). Reject: deleted with its photos.</div></div>";
}
// The forecast's models (the app's ForecastSpots.json) for the weights' boxes.
const WEIGHT_MODELS = [["dmi_harmonie_arome_europe", "HARM-DK 2 km"], ["metno_nordic", "MET Nordic 1 km"], ["aladin", "ALADIN 2.3 km"],
  ["fmi", "HARM-FI 2.5 km"], ["meteofrance_arome_france_hd", "AROME-FR 1.5 km"], ["knmi_harmonie_arome_netherlands", "HARM-NL 2 km"],
  ["ukmo_uk_deterministic_2km", "UKV 2 km"], ["icon_d2", "ICON-D2 2.2 km"], ["meteoswiss_icon_ch1", "ICON-CH1 1 km"],
  ["meteoswiss_icon_ch2", "ICON-CH2 2 km"], ["italia_meteo_arpae_icon_2i", "ICON-2I 2.2 km"], ["meteofrance_arome_france", "AROME-FR 2.5 km"],
  ["ncep_hrrr_conus", "HRRR 3 km"], ["gem_hrdps_continental", "HRDPS 2.5 km"], ["knmi_harmonie_arome_europe", "HARMONIE 5 km"],
  ["icon_eu", "ICON 7 km"], ["ecmwf_ifs", "ECMWF 9 km"], ["gfs_global", "GFS 13 km"], ["icon_global", "ICON 13 km"], ["gem_global", "GDPS 15 km"],
  ["wrf_tarifa", "WRF 1 km (ours, Tarifa)"]];
// A box per model (percent; empty: not used), prefilled from the weights (shares).
function weightsGrid(prefix, weights) {
  return '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:4px 12px;margin:6px 0">' +
    WEIGHT_MODELS.map(([key, name]) => {
      const v = weights && weights[key] ? Math.round(weights[key] * 1000) / 10 : "";
      return '<label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--label);margin:0">' +
        '<input data-weight="' + prefix + '" data-key="' + key + '" value="' + v + '" style="width:64px;display:inline-block" inputmode="decimal"> % ' + esc(name) + "</label>";
    }).join("") + "</div>";
}
function readWeights(prefix) {
  const out = {};
  for (const input of document.querySelectorAll('input[data-weight="' + prefix + '"]')) {
    const v = parseFloat(String(input.value).replace(",", "."));
    if (Number.isFinite(v) && v > 0) out[input.dataset.key] = v;
  }
  return out;
}

async function spotAction(body) {
  const r = await fetch("/admin/api/spots", { method: "POST", headers: { "content-type": "application/json", "x-shorewind-admin": "1" }, body: JSON.stringify(body) });
  const reply = await r.json();
  if (!reply.ok) alert("Not done: " + reply.error);
  return reply;
}
// The spot info editor (the app's About on the spot page).
const INFO_LABELS = {
  sports: [["kite", "Kite"], ["wing", "Wing"], ["windsurf", "Windsurf"], ["sup", "SUP"], ["surf", "Surf"], ["snowkite", "Snowkite"]],
  levels: [["beginner", "Beginner"], ["intermediate", "Intermediate"], ["expert", "Expert"]],
  water: [["flat", "Flat"], ["chop", "Chop"], ["smallWaves", "Small waves"], ["bigWaves", "Big waves"]],
  beach: [["sand", "Sand"], ["stones", "Stones"], ["grass", "Grass"]],
  hazards: [["nets", "Fishing nets"], ["rocks", "Rocks"], ["piers", "Piers"], ["shipping", "Shipping"], ["current", "Strong current"], ["sandbar", "Sandbar"]],
  amenities: [["parking", "Parking"], ["toilet", "Toilet"], ["shower", "Shower"], ["food", "Food"], ["school", "School"], ["rental", "Rental"], ["shop", "Shop"]],
};
function ticks(group, chosen, single) {
  return '<div data-group="' + group + '"' + (single ? ' data-single="1"' : "") + ">" + INFO_LABELS[group].map((o) =>
    '<button type="button" class="tick' + ((chosen || []).includes(o[0]) ? " on" : "") + '" data-value="' + o[0] + '">' + esc(o[1]) + "</button>").join("") + "</div>";
}
function ruleRow(r) {
  return '<div class="rule"><input class="rt" placeholder="A rule, e.g. No kiting in the swimming zone by the pier" value="' + esc((r && r.text) || "") + '">' +
    '<input class="d rf" placeholder="from 06-01" value="' + esc((r && r.from) || "") + '"><input class="d rto" placeholder="to 08-31" value="' + esc((r && r.to) || "") + '"></div>';
}
INFO_LABELS.crowds = [["quiet", "Quiet"], ["medium", "Medium"], ["busy", "Busy"]];
INFO_LABELS.standing = [["yes", "Yes"], ["no", "No"]];
INFO_LABELS.launch = [["big", "Big launch area"]];
INFO_LABELS.tide = [["show", "Show"], ["hide", "Hide"]];
INFO_LABELS.goodMonths = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].map((m, n) => [String(n + 1), m]);
// In the panel: drawn into "box"; hooks.saved() after a save or a move, hooks.close() on Close.
async function openInfo(code, box, hooks) {
  box.innerHTML = '<p class="empty">Reading the spot…</p>';
  const d = await (await fetch("/admin/api/spots/info?code=" + encodeURIComponent(code), { cache: "no-store" })).json();
  if (d.error) { box.textContent = "Can't load: " + d.error; return; }
  const i = d.info || {};
  const rules = (i.rules || []).concat([{}]);
  box.innerHTML = "<h3>SPOT INFO · " + esc(d.name).toUpperCase() + ' <span class="dim">' + (d.draft ? "draft, not published" : d.published ? "published" : "nothing yet") + "</span></h3>" +
    '<label style="display:block;font-size:12px;color:var(--muted);margin:4px 0 4px">Photos and videos (the first is the cover; ← → to order; up to 15 photos and 3 videos)</label>' +
    '<div class="photos" id="i-photos"></div>' +
    '<label class="tick" style="display:inline-block;cursor:pointer">+ Add photos<input type="file" id="i-addphoto" accept="image/*" multiple style="display:none"></label>' +
    '<label class="tick" style="display:inline-block;cursor:pointer">+ Add video<input type="file" id="i-addvideo" accept="video/*" style="display:none"></label>' +
    '<span class="note" id="i-photonote" style="margin-left:8px"></span>' +
    '<div id="i-conv"></div>' +
    '<div class="ed"><div>' +
    '<label>About (English, a few lines)</label><textarea id="i-about">' + esc(i.about || "") + "</textarea>" +
    "<label>Sports</label>" + ticks("sports", i.sports) +
    "<label>Level</label>" + ticks("levels", i.levels) +
    '<label>Possible wind directions: kiteable, where the wind comes from (degrees, clockwise)</label><input id="i-kiteable" value="' + esc(d.kiteable) + '" placeholder="185-355">' +
    '<label>Best wind directions: where it works best, inside the possible ones</label><input id="i-best" value="' + esc(d.best || "") + '" placeholder="225-300">' +
    '<label>Offshore directions: from land (empty: reckoned from the sea direction, within 60° of straight from land)</label><input id="i-offshore" value="' + esc(d.offshore || "") + '" placeholder="45-160">' +
    '<div class="note">E.g. 185-355 or 20-60, 200-340. ' + (d.offshoreBearing != null ? "Open sea is towards " + Math.round(d.offshoreBearing) + "°. " : "") + "Saved when you publish. Best: the app's rose shows them darker, and hours from the other possible directions get one star fewer.</div>" +
    '<div class="rosebox"><div id="i-rose"></div><div class="note" style="margin:0">Tap a sector: not working → possible → best → not working (the fields follow; ' +
    "or type in them and the rose follows). Red: offshore, from land. Where the wind comes from.</div></div>" +
    "<label>Water</label>" + ticks("water", i.water) +
    "<label>Water level in the forecast: every rider's default here (none ticked: the app decides, shown where the tide is big; a rider's own choice wins)</label>" +
    ticks("tide", i.tide ? [i.tide] : i.levelMatters ? ["show"] : [], true) +
    "<label>Standing depth</label>" + ticks("standing", i.standing === true ? ["yes"] : i.standing === false ? ["no"] : [], true) +
    '<input id="i-standing" placeholder="e.g. first 40 m" value="' + esc(i.standingNote || "") + '" style="margin-top:4px">' +
    "</div><div>" +
    "<label>Beach</label>" + ticks("beach", i.beach) + ticks("launch", i.bigLaunch ? ["big"] : []) +
    "<label>Crowds</label>" + ticks("crowds", i.crowds ? [i.crowds] : [], true) +
    '<label>Rules (dates: only between them each year, MM-DD; empty: always)</label><div id="i-rules">' + rules.map(ruleRow).join("") + "</div>" +
    '<button type="button" id="i-addrule">+ Add a rule</button>' +
    "<label>Hazards</label>" + ticks("hazards", i.hazards) +
    '<input id="i-hazard" placeholder="More about them, e.g. nets 300-500 m out in spring and autumn" value="' + esc(i.hazardNote || "") + '" style="margin-top:4px">' +
    "<label>Amenities (near the launch)</label>" + ticks("amenities", i.amenities) +
    "</div></div>" +
    '<label style="display:block;font-size:12px;color:var(--muted);margin:14px 0 4px">On the spot: pins and zones (tap a tool, then the map; drag a pin to move it, tap it to add a note)</label>' +
    '<div class="maptools" id="i-tools"></div>' +
    '<div id="i-map" style="height:420px;border-radius:10px;border:1px solid var(--edge);margin-top:6px"></div>' +
    '<div class="note" id="i-mapnote"></div>' +
    '<div class="note" style="margin-top:6px">Where the spot is (the blue dot: its marker, and where the forecast is read): drag it, or type ' +
    '<input id="i-lat" value="' + d.lat + '" style="width:92px;display:inline-block"> <input id="i-lon" value="' + d.lon + '" style="width:92px;display:inline-block"> ' +
    '<button type="button" id="i-move">Move the spot here</button> <span id="i-movenote"></span></div>' +
    '<label style="display:block;font-size:12px;color:var(--muted);margin:14px 0 4px">Best months (from 10 years of hourly wind history at the spot, ERA5 via Open-Meteo)</label>' +
    '<div id="i-months"></div>' +
    '<button type="button" id="i-compute">Compute from history</button><span class="note" id="i-monthsnote" style="margin-left:8px"></span>' +
    '<label style="display:block;font-size:12px;color:var(--muted);margin:14px 0 4px">Best months by experience (riders\' and yours, for when there\'s no history; the old editor dropped them on save)</label>' +
    ticks("goodMonths", (i.goodMonths || []).map(String)) +
    '<label style="display:block;font-size:12px;color:var(--muted);margin:14px 0 4px">Model weights (the blend of the spot forecast, and the weights of the Mix map and of new spots round here; percent, only the ratios count)</label>' +
    weightsGrid("i", d.weights) +
    '<input id="i-wsource" placeholder="Where from (a note)" value="' + esc(d.weightsSource || "") + '" style="width:260px;display:inline-block"> ' +
    '<button type="button" id="i-wsave">Save weights</button> <span class="note" id="i-wout"></span>' +
    '<div style="margin-top:12px"><button type="button" id="i-publish" class="primary">Publish</button><button type="button" id="i-draft">Save draft</button><button type="button" id="i-close">Close</button>' +
    '<span class="note" id="i-out" style="margin-left:8px"></span></div>' +
    '<div class="note">Published info reaches the app with the spot catalog (phones check every few hours); a draft stays here.</div>';
  for (const b of box.querySelectorAll("button.tick")) {
    b.onclick = () => {
      const group = b.parentElement;
      if (group.dataset.single) for (const other of group.querySelectorAll("button.tick")) if (other !== b) other.classList.remove("on");
      b.classList.toggle("on");
    };
  }
  document.getElementById("i-addrule").onclick = () => document.getElementById("i-rules").insertAdjacentHTML("beforeend", ruleRow({}));
  // The wind rose for the two direction fields, as in the app; a tap on a sector changes it.
  const drawRose = () => {
    const possible = readSectorsHere(document.getElementById("i-kiteable").value) || [];
    const best = readSectorsHere(document.getElementById("i-best").value) || [];
    const offshore = readSectorsHere(document.getElementById("i-offshore").value) || [];
    document.getElementById("i-rose").innerHTML = roseSvg(possible, best, d.offshoreBearing, offshore);
    for (const seg of document.querySelectorAll("#i-rose [data-seg]")) {
      seg.onclick = () => {
        const n = +seg.dataset.seg;
        const p = roseSegments(possible), b = roseSegments(best);
        // Offshore as shown: the marked sectors, else the reckoned ones (so marking one keeps the rest).
        const o = offshore.length ? roseSegments(offshore) : roseOffshore(d.offshoreBearing).map((x, i) => x && !p[i] && !b[i]);
        const wasP = p[n] || b[n], wasB = b[n], wasO = o[n];
        // not working -> possible -> best -> offshore -> not working
        if (o[n] && !p[n] && !b[n]) { o[n] = false; } else if (b[n]) { b[n] = false; p[n] = false; o[n] = true; } else if (p[n]) { b[n] = true; } else { p[n] = true; o[n] = false; }
        // Only the field that changed is rewritten (typed edges elsewhere stay as typed).
        if ((p[n] || b[n]) !== wasP) document.getElementById("i-kiteable").value = segmentsText(p.map((x, i) => x || b[i]));
        if (b[n] !== wasB) document.getElementById("i-best").value = segmentsText(b);
        if (o[n] !== wasO) document.getElementById("i-offshore").value = segmentsText(o);
        drawRose();
      };
    }
  };
  document.getElementById("i-kiteable").oninput = drawRose;
  document.getElementById("i-best").oninput = drawRose;
  document.getElementById("i-offshore").oninput = drawRose;
  drawRose();
  // Photos and videos in one row (the owner, 23.10.2026: videos, and vertical ones shown whole): saved as the photos
  // in their order and the videos with "at", the number of photos before each.
  const media = (i.photos || []).map((x) => ({ ...x, kind: "photo" }));
  for (const v of (i.videos || []).slice().sort((a, b) => (a.at || 0) - (b.at || 0))) {
    let k = 0, n = 0;
    while (k < media.length && n < (v.at || 0)) { if (media[k].kind === "photo") n++; k++; }
    while (k < media.length && media[k].kind === "video") k++;
    media.splice(k, 0, { ...v, kind: "video" });
  }
  // The photos it opened with: saving keeps any added elsewhere meanwhile (an approved suggestion's).
  const photosLoaded = media.filter((x) => x.kind === "photo").map((x) => x.url);
  const drawPhotos = () => {
    document.getElementById("i-photos").innerHTML = media.map((x, n) => {
      const src = esc(x.kind === "video" ? x.poster : x.url), wide = !(x.w && x.h) || x.w >= x.h;
      const meta = x.kind === "video" ? "video · " + x.w + "×" + x.h + " · " + (x.sound ? "sound" : "no sound") + " · " + x.mb + " MB"
        : "photo" + (x.w && x.h ? " · " + x.w + "×" + x.h + (wide ? "" : " portrait") : "");
      return '<div class="ph"><div class="tb">' + (wide ? '<img class="fill" src="' + src + '">' : '<img class="blur" src="' + src + '"><img class="whole" src="' + src + '">') +
        (x.kind === "video" ? '<span class="vplay">▶</span><span class="vlen">' + videoClock(x.s) + "</span>" : "") + "</div>" +
        '<div class="phb"><button type="button" class="mini" data-pl="' + n + '">←</button>' +
        '<button type="button" class="mini" data-pr="' + n + '">→</button><button type="button" class="mini" data-px="' + n + '">✕</button></div>' +
        '<input class="phc" data-pc="' + n + '" placeholder="' + (x.kind === "video" ? "Video" : "Photo") + ' by…" value="' + esc(x.credit || "") + '">' +
        (n === 0 ? '<span class="cover">cover</span>' : "") + '<div class="phm">' + meta + "</div></div>";
    }).join("") || '<div class="note" style="margin:0">No photos yet.</div>';
    const move = (n, by) => { const m = n + by; if (m < 0 || m >= media.length) return; [media[n], media[m]] = [media[m], media[n]]; drawPhotos(); };
    for (const b of box.querySelectorAll("button[data-pl]")) b.onclick = () => move(+b.dataset.pl, -1);
    for (const b of box.querySelectorAll("button[data-pr]")) b.onclick = () => move(+b.dataset.pr, 1);
    for (const b of box.querySelectorAll("button[data-px]")) b.onclick = () => { media.splice(+b.dataset.px, 1); drawPhotos(); };
    for (const c of box.querySelectorAll("input[data-pc]")) c.oninput = () => { media[+c.dataset.pc].credit = c.value; };
  };
  drawPhotos();
  const savedMedia = () => {
    const photos = [], videos = [];
    for (const x of media) {
      const { kind, ...rest } = x;
      if (kind === "video") videos.push({ ...rest, at: photos.length }); else photos.push(rest);
    }
    return { photos, videos };
  };
  // A video: made small here (smallVideo), its part and poster picked first, then the poster and the video uploaded.
  document.getElementById("i-addvideo").onchange = (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    if (media.filter((x) => x.kind === "video").length >= 3) { document.getElementById("i-photonote").textContent = "Three videos at most: remove one first."; return; }
    videoConverter(document.getElementById("i-conv"), file, code, (added) => {
      media.push({ ...added, kind: "video" });
      drawPhotos();
      document.getElementById("i-photonote").textContent = "Added: save or publish to keep it.";
    });
  };
  document.getElementById("i-addphoto").onchange = async (e) => {
    const note = document.getElementById("i-photonote");
    for (const file of e.target.files) {
      note.textContent = "Uploading " + file.name + "…";
      try {
        const small = await smallerJpeg(file, 1600);
        const r = await fetch("/admin/api/spots/photo?code=" + encodeURIComponent(code), { method: "POST", headers: { "content-type": "image/jpeg", "x-shorewind-admin": "1" }, body: small.blob });
        const reply = await r.json();
        if (!reply.ok) throw new Error(reply.error);
        media.push({ url: reply.url, w: small.w, h: small.h, kind: "photo" });
        drawPhotos();
        note.textContent = "Added: save or publish to keep them.";
      } catch (err) { note.textContent = "Not added: " + err.message; }
    }
    e.target.value = "";
  };
  const onMap = await spotMap(d, i, box);
  let months = i.months || null;
  const drawMonths = () => {
    const names = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];
    document.getElementById("i-months").innerHTML = months
      ? '<div class="months">' + months.good.map((v, n) => '<div><i style="height:' + Math.max(2, (85 * v) / Math.max(1, ...months.good)) + '%"></i><b>' + v + "</b><span>" + names[n] + "</span></div>").join("") + "</div>" +
        '<div class="note">Days with ' + months.hours + "+ hours of " + months.min + "–" + months.max + " m/s from a good direction, " + esc(months.years) + ".</div>"
      : '<div class="note" style="margin:0">Not computed yet.</div>';
  };
  drawMonths();
  document.getElementById("i-compute").onclick = async () => {
    const note = document.getElementById("i-monthsnote");
    const sectors = readSectorsHere(document.getElementById("i-kiteable").value);
    if (sectors === null) { note.textContent = "Good directions: write them like 185-355, 20-60"; return; }
    note.textContent = "Fetching 10 years of hourly wind…";
    try {
      months = await bestMonths(d.lat, d.lon, sectors, note);
      drawMonths();
      note.textContent = "Done: save or publish to keep it." + (sectors.length ? "" : " (No good directions set: every direction counted.)");
    } catch (err) { note.textContent = "Couldn't compute: " + err.message; }
  };
  document.getElementById("i-close").onclick = () => hooks.close();
  const save = async (publish) => {
    const chosen = (group) => [...box.querySelectorAll('[data-group="' + group + '"] button.on')].map((b) => b.dataset.value);
    const standing = chosen("standing")[0];
    const info = {
      about: document.getElementById("i-about").value, sports: chosen("sports"), levels: chosen("levels"), water: chosen("water"),
      standing: standing === "yes" ? true : standing === "no" ? false : null, standingNote: document.getElementById("i-standing").value,
      beach: chosen("beach"), bigLaunch: chosen("launch").length > 0, tide: chosen("tide")[0] || null, levelMatters: chosen("tide")[0] === "show", crowds: chosen("crowds")[0] || null,
      rules: [...box.querySelectorAll("#i-rules .rule")].map((r) => ({ text: r.querySelector(".rt").value, from: r.querySelector(".rf").value.trim(), to: r.querySelector(".rto").value.trim() })),
      hazards: chosen("hazards"), hazardNote: document.getElementById("i-hazard").value, amenities: chosen("amenities"),
      pins: onMap.pins, zones: onMap.zones, ...savedMedia(), months, goodMonths: chosen("goodMonths").map(Number),
    };
    const reply = await spotAction({ action: "info", code, info, publish, photosLoaded, kiteable: document.getElementById("i-kiteable").value,
                                     best: document.getElementById("i-best").value, offshore: document.getElementById("i-offshore").value });
    if (reply.ok) {
      document.getElementById("i-out").textContent = (publish ? "Published " : "Draft saved ") + new Date().toLocaleTimeString();
      hooks.saved();
    }
  };
  // Publish asks once more, as every change in the panel does (it goes to every rider).
  const publishButton = document.getElementById("i-publish");
  let asking = null;
  publishButton.onclick = () => {
    if (!asking) {
      publishButton.textContent = "Publish to every rider? Click again";
      asking = setTimeout(() => { asking = null; publishButton.textContent = "Publish"; }, 6000);
      return;
    }
    clearTimeout(asking);
    asking = null;
    publishButton.textContent = "Publish";
    save(true);
  };
  document.getElementById("i-draft").onclick = () => save(false);
  document.getElementById("i-wsave").onclick = async () => {
    const out = document.getElementById("i-wout");
    const reply = await spotAction({ action: "weights", code, weights: readWeights("i"), source: document.getElementById("i-wsource").value });
    out.textContent = reply.ok ? "Saved " + new Date().toLocaleTimeString() + ": the app and the map get them within a few hours."
      : "Not saved: " + (reply.error || "?");
  };
  document.getElementById("i-move").onclick = async () => {
    const lat = +document.getElementById("i-lat").value, lon = +document.getElementById("i-lon").value;
    const out = document.getElementById("i-movenote");
    let reply = await (await fetch("/admin/api/spots", { method: "POST", headers: { "content-type": "application/json", "x-shorewind-admin": "1" },
                                                    body: JSON.stringify({ action: "move", code, lat, lon }) })).json();
    if (reply.far) {
      if (!confirm(reply.error + " from where it is. The forecast's model points were picked for the old place; " +
                   "so far away they should be picked again. Move it anyway?")) { out.textContent = "Not moved."; return; }
      reply = await spotAction({ action: "move", code, lat, lon, far: true });
    } else if (!reply.ok) {
      alert("Not done: " + reply.error);
    }
    if (reply.ok) {
      d.lat = lat;
      d.lon = lon;
      out.textContent = "Moved " + reply.distance + " m (" + new Date().toLocaleTimeString() + "); the app gets it with the spot list within minutes.";
      hooks.saved();
    }
  };
  box.scrollIntoView({ behavior: "smooth" });
}
// The rose's 16 sectors (22.5° each, centred on N, NNE...): which ones the sectors cover.
const inSectors = (deg, sectors) => sectors.some(([a, b]) => (a === b ? true : a <= b ? deg >= a && deg <= b : deg >= a || deg <= b));
function roseSegments(sectors) {
  return Array.from({ length: 16 }, (_, i) => inSectors(i * 22.5, sectors));
}
// Sectors -> "355-15, 160-185": each run of chosen sectors from its first's start to its last's end.
function segmentsText(on) {
  if (on.every(Boolean)) return "0-360";
  if (!on.some(Boolean)) return "";
  const start = on.findIndex((x, i) => x && !on[(i + 15) % 16]);
  const runs = [];
  for (let k = 0; k < 16; k++) {
    const i = (start + k) % 16;
    if (!on[i]) continue;
    if (on[(i + 15) % 16] && runs.length) runs[runs.length - 1][1] = i; else runs.push([i, i]);
  }
  const edge = (x) => Math.round(((x % 360) + 360) % 360);
  return runs.map(([a, b]) => edge(a * 22.5 - 11.25) + "-" + edge(b * 22.5 + 11.25)).join(", ");
}
// The 16 sectors within 60° of straight from land (the app's offshore when none are marked).
function roseOffshore(sea) {
  const land = sea == null ? null : (sea + 180) % 360;
  return Array.from({ length: 16 }, (_, i) => land != null && Math.min(Math.abs(i * 22.5 - land) % 360, 360 - (Math.abs(i * 22.5 - land) % 360)) <= 60);
}

function roseSvg(possible, best, sea, offshore) {
  const size = 220, c = size / 2, inner = 48, outer = 74;
  const at = (deg, r) => [c + Math.sin(deg * Math.PI / 180) * r, c - Math.cos(deg * Math.PI / 180) * r];
  const land = sea == null ? null : (sea + 180) % 360;
  let out = "";
  for (let i = 0; i < 16; i++) {
    const mid = i * 22.5, from = mid - 10.5, to = mid + 10.5;
    const pts = [];
    for (let k = 0; k <= 6; k++) pts.push(at(from + (to - from) * k / 6, outer));
    for (let k = 6; k >= 0; k--) pts.push(at(from + (to - from) * k / 6, inner));
    const off = offshore && offshore.length ? inSectors(mid, offshore) : land != null && Math.min(Math.abs(mid - land) % 360, 360 - (Math.abs(mid - land) % 360)) <= 60;
    const color = inSectors(mid, best) ? "#16a34a" : inSectors(mid, possible) ? (best.length ? "#a3d977" : "var(--green)") : off ? "var(--red)" : "var(--edge)";
    out += '<polygon data-seg="' + i + '" points="' + pts.map((q) => q[0].toFixed(1) + "," + q[1].toFixed(1)).join(" ") + '" fill="' + color +
      '" stroke="var(--bg)" stroke-width="1" style="cursor:pointer"><title>' + Math.round(mid) + "°</title></polygon>";
  }
  for (const [name, deg] of [["N", 0], ["E", 90], ["S", 180], ["W", 270]]) {
    const q = at(deg, outer + 13);
    out += '<text x="' + q[0].toFixed(1) + '" y="' + (q[1] + 4).toFixed(1) + '" text-anchor="middle" font-size="12" font-weight="700" fill="var(--dim)">' + name + "</text>";
  }
  if (sea != null) {
    const q = at(sea, inner - 12), r = at(sea, 16);
    out += '<line x1="' + r[0].toFixed(1) + '" y1="' + r[1].toFixed(1) + '" x2="' + q[0].toFixed(1) + '" y2="' + q[1].toFixed(1) + '" stroke="var(--link)" stroke-width="2"/>' +
      '<text x="' + c + '" y="' + (c + 4) + '" text-anchor="middle" font-size="10" fill="var(--link)">sea</text>';
  }
  const key = (col, t) => '<span class="rk"><i style="background:' + col + '"></i>' + t + "</span>";
  return '<svg viewBox="0 0 ' + size + " " + size + '" width="' + size + '" height="' + size + '">' + out + "</svg>" +
    '<div class="rkeys">' + (best.length ? key("#16a34a", "best") + key("#a3d977", "possible") : key("var(--green)", "possible")) +
    key("var(--red)", "offshore") + key("var(--edge)", "not working") + "</div>";
}
// "185-355, 20-60" -> [[185, 355], [20, 60]] (as on the server); null: unreadable.
function readSectorsHere(value) {
  const sectors = [];
  for (const part of String(value || "").split(",").map((x) => x.trim()).filter(Boolean)) {
    const m = part.match(/^(\d{1,3})\s*-\s*(\d{1,3})$/);
    if (!m || +m[1] > 360 || +m[2] > 360) return null;
    sectors.push([+m[1] % 360, +m[2] % 360]);
  }
  return sectors;
}
// Best months from history (here in the browser, the Worker has too little time for it): ten
// years of ERA5's hourly wind at the spot's cell (the owner, 19.10.2026: off Open-Meteo's archive
// API, for its licence; baltic-aladin era5_point.py reads it from Open-Meteo's open data on AWS, kept
// in our files, /api/climate); a good day has at least 3 daytime hours (9-19 local) of 6-17 m/s from a
// good direction; each month's share of good days.
async function bestMonths(lat, lon, sectors, note) {
  let data = null;
  for (let tries = 0; tries < 40 && !data; tries++) {
    const r = await fetch("/admin/api/climate", { method: "POST", headers: { "content-type": "application/json", "x-shorewind-admin": "1" },
      body: JSON.stringify({ lat, lon, start: tries === 0 }) });
    const reply = await r.json();
    if (reply.error) throw new Error(reply.error);
    if (reply.speed) { data = reply; break; }
    if (note) note.textContent = "Reading 10 years of ERA5 wind on the helper (about a minute)…";
    await new Promise((done) => setTimeout(done, 10000));
  }
  if (!data) throw new Error("the helper's ERA5 job hasn't finished: try again in a few minutes");
  const inSector = (d) => !sectors.length || sectors.some(([a, b]) => (a <= b ? d >= a && d <= b : d >= a || d <= b));
  // Local days and hours in the place's zone (its offset taken at each day's noon).
  const zone = data.zone || "UTC";
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23" });
  const offsetAt = (t) => {
    const p = Object.fromEntries(parts.formatToParts(new Date(t * 1000)).map((x) => [x.type, x.value]));
    return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour) / 1000 - Math.floor(t / 3600) * 3600;
  };
  const days = {};
  const v = data.speed, dir = data.direction;
  let offset = offsetAt(data.start), offsetDay = -1;
  for (let n = 0; n < v.length; n++) {
    const t = data.start + n * 3600;
    const utcDay = Math.floor(t / 86400);
    if (utcDay !== offsetDay) { offset = offsetAt(utcDay * 86400 + 43200); offsetDay = utcDay; }
    const local = new Date((t + offset) * 1000);
    const hour = local.getUTCHours();
    const day = local.toISOString().slice(0, 10);
    if (!(day in days)) days[day] = 0;
    const speed = v[n] == null ? null : v[n] / 10;
    if (hour >= 9 && hour <= 19 && speed != null && speed >= 6 && speed <= 17 && dir[n] != null && inSector(dir[n])) days[day]++;
  }
  const good = new Array(12).fill(0), all = new Array(12).fill(0);
  for (const [day, hours] of Object.entries(days)) {
    const m = +day.slice(5, 7) - 1;
    all[m]++;
    if (hours >= 3) good[m]++;
  }
  return { good: good.map((g, m) => (all[m] ? Math.round((100 * g) / all[m]) : 0)), min: 6, max: 17, hours: 3, years: data.years };
}
// A video made small in this browser (the owner, 23.10.2026: "convert video automatically into lower acceptable
// quality ... but still have good quality"; "1080p for all", sound "drop"): played in the given <video> element (it
// knows the phone's rotation, so a vertical clip comes out upright), each frame drawn onto a canvas and encoded by the
// browser's own H.264 encoder (WebCodecs), at most 1080 on the short side, 30 fps, about 5 Mbit/s at 1080p, from
// "start" for "len" seconds; the sound (AAC) only when asked. Then a poster JPEG at "posterAt". Takes about the
// clip's length (it plays through once); the tab must stay in front meanwhile.
const MP4_MUXER = "https://cdn.jsdelivr.net/npm/mp4-muxer@5.1.3/build/mp4-muxer.mjs";
const VIDEO_SHORT = 1080, VIDEO_FPS = 30, VIDEO_RATE = 5e6;
function videoSize(sw, sh) {
  const k = Math.min(1, VIDEO_SHORT / Math.min(sw, sh));
  return { w: Math.round((sw * k) / 2) * 2, h: Math.round((sh * k) / 2) * 2 };
}
const videoRate = (w, h) => Math.round(Math.max(1.5e6, (VIDEO_RATE * w * h) / (1920 * 1080)));
function videoEvent(v, name) {
  return new Promise((ok, no) => {
    const done = () => { v.removeEventListener(name, done); v.removeEventListener("error", bad); ok(); };
    const bad = () => { v.removeEventListener(name, done); v.removeEventListener("error", bad); no(new Error("the browser can't read this video")); };
    v.addEventListener(name, done);
    v.addEventListener("error", bad);
  });
}
async function videoSeek(v, t) {
  if (Math.abs(v.currentTime - t) < 0.001 && v.readyState >= 2) return;
  const seeked = videoEvent(v, "seeked");
  v.currentTime = t;
  await seeked;
}
async function aacSupported(sampleRate, channels) {
  if (!window.AudioEncoder) return false;
  try { return (await AudioEncoder.isConfigSupported({ codec: "mp4a.40.2", sampleRate, numberOfChannels: channels, bitrate: 128000 })).supported; }
  catch (e) { return false; }
}
async function smallVideo(file, v, opt, progress) {
  if (!window.VideoEncoder) throw new Error("this browser can't make videos: use Chrome or Safari");
  const { Muxer, ArrayBufferTarget } = await import(MP4_MUXER);
  const { w, h } = videoSize(v.videoWidth, v.videoHeight);
  const bitrate = videoRate(w, h);
  let config = null;
  for (const codec of ["avc1.640028", "avc1.4d0028", "avc1.42e028"]) {
    const c = { codec, width: w, height: h, bitrate, framerate: VIDEO_FPS, avc: { format: "avc" }, latencyMode: "quality" };
    try { if ((await VideoEncoder.isConfigSupported(c)).supported) { config = c; break; } } catch (e) {}
  }
  if (!config) throw new Error("this browser has no H.264 encoder for " + w + "×" + h);
  // The sound: decoded whole by the browser, the kept part encoded as AAC (stereo at most).
  let audio = null;
  if (opt.sound) {
    progress(0, "reading the sound…");
    try {
      const actx = new (window.AudioContext || window.webkitAudioContext)();
      const buffer = await actx.decodeAudioData(await file.arrayBuffer());
      actx.close();
      const channels = Math.min(2, buffer.numberOfChannels);
      if (await aacSupported(buffer.sampleRate, channels)) audio = { buffer, channels, rate: buffer.sampleRate };
      else throw new Error("this browser can't encode AAC sound: untick Keep sound");
    } catch (e) {
      if (String(e.message).indexOf("AAC") >= 0) throw e;
      audio = null;  // no sound track in the file
    }
  }
  const target = new ArrayBufferTarget();
  const muxer = new Muxer({ target, video: { codec: "avc", width: w, height: h, frameRate: VIDEO_FPS },
    audio: audio ? { codec: "aac", numberOfChannels: audio.channels, sampleRate: audio.rate } : undefined,
    fastStart: "in-memory", firstTimestampBehavior: "offset" });
  let failed = null;
  const encoder = new VideoEncoder({ output: (chunk, meta) => muxer.addVideoChunk(chunk, meta), error: (e) => { failed = e; } });
  encoder.configure(config);
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const g = canvas.getContext("2d");
  v.muted = true;
  await videoSeek(v, opt.start);
  progress(0, "converting in this browser…");
  // Frames are taken by their time in the clip, so pausing loses nothing. Playing, a frame comes with each one the
  // browser shows (requestVideoFrameCallback); the clip pauses while the encoder is behind and while the tab is in the
  // background. Frames the browser didn't hand over (a slow moment; a tab it slowed, or stopped drawing, without saying
  // so) are taken one by one by seeking, which nothing slows: a second's worth, then playing again, or on to the end
  // when playing gave next to nothing. Each frame goes in a slot of an even VIDEO_FPS grid (the MP4 counts time in
  // those steps; a 60 fps clip keeps every second frame), one frame a slot.
  const end = Math.round(opt.len * VIDEO_FPS);
  let frames = 0, slot = -1, held = false, lastTake = Date.now(), played = 0;
  const take = (n) => {
    g.drawImage(v, 0, 0, w, h);
    const frame = new VideoFrame(canvas, { timestamp: Math.round((n * 1e6) / VIDEO_FPS) });
    encoder.encode(frame, { keyFrame: frames % (VIDEO_FPS * 2) === 0 });
    frame.close();
    frames++;
    played++;
    slot = n;
    lastTake = Date.now();
    progress(Math.min(1, (n + 1) / end));
  };
  await new Promise((done, fail) => {
    let over = false, waiting = false, watch = null;
    const seen = () => {
      if (document.hidden) { v.pause(); progress(Math.max(0, slot + 1) / end, "paused while this tab is in the background: come back to it to go on"); }
      else resume();
    };
    const stop = (e) => {
      if (over) return;
      over = true;
      v.pause();
      v.onended = null;
      clearInterval(watch);
      document.removeEventListener("visibilitychange", seen);
      if (e) fail(e); else done();
    };
    const resume = () => {
      if (over || held || document.hidden) return;
      lastTake = Date.now();
      played = 0;
      v.play().catch(stop);
    };
    // One frame callback waiting at a time; whoever holds the clip asks again when it lets go.
    const ask = () => {
      if (waiting || over) return;
      waiting = true;
      v.requestVideoFrameCallback((now, meta) => { waiting = false; step(meta); });
    };
    const catchUp = async () => {
      if (held || over) return;
      held = true;
      v.pause();
      const toEnd = played < VIDEO_FPS;
      try {
        for (let k = 0; (toEnd || k < VIDEO_FPS) && !over; k++) {
          const n = slot + 1;
          if (n >= end) { stop(); return; }
          await videoSeek(v, opt.start + n / VIDEO_FPS);
          while (encoder.encodeQueueSize > 12) await new Promise((ok) => encoder.addEventListener("dequeue", ok, { once: true }));
          if (failed) throw failed;
          take(n);
        }
      } catch (e) { stop(e); return; }
      held = false;
      ask();
      resume();
    };
    const step = (meta) => {
      if (over || held) return;
      if (failed) { stop(failed); return; }
      const n = Math.round((meta.mediaTime - opt.start) * VIDEO_FPS);
      // Over one frame missed: taken by seeking (one missed frame is shown a little longer, which no one sees).
      if (Math.min(n, end) - slot > 2) { catchUp(); return; }
      if (n >= end) { stop(); return; }
      if (n > slot) take(n);
      if (encoder.encodeQueueSize > 12) {
        held = true;
        v.pause();
        const go = () => { if (encoder.encodeQueueSize <= 4) { encoder.removeEventListener("dequeue", go); held = false; ask(); resume(); } };
        encoder.addEventListener("dequeue", go);
        return;
      }
      ask();
    };
    v.onended = () => catchUp();
    watch = setInterval(() => { if (!over && !held && !document.hidden && Date.now() - lastTake > 1200) catchUp(); }, 500);
    document.addEventListener("visibilitychange", seen);
    ask();
    resume();
  });
  await encoder.flush();
  encoder.close();
  if (failed) throw failed;
  if (!frames) throw new Error("no frames came out: keep this tab in front while it converts");
  if (audio) {
    const aenc = new AudioEncoder({ output: (chunk, meta) => muxer.addAudioChunk(chunk, meta), error: (e) => { failed = e; } });
    aenc.configure({ codec: "mp4a.40.2", sampleRate: audio.rate, numberOfChannels: audio.channels, bitrate: 128000 });
    const from = Math.floor(opt.start * audio.rate), to = Math.min(audio.buffer.length, Math.floor((opt.start + opt.len) * audio.rate));
    const parts = [];
    for (let c = 0; c < audio.channels; c++) parts.push(audio.buffer.getChannelData(c));
    for (let i = from; i < to; i += 1024) {
      const n = Math.min(1024, to - i), data = new Float32Array(n * audio.channels);
      for (let c = 0; c < audio.channels; c++) data.set(parts[c].subarray(i, i + n), c * n);
      const chunk = new AudioData({ format: "f32-planar", sampleRate: audio.rate, numberOfFrames: n, numberOfChannels: audio.channels,
        timestamp: Math.round(((i - from) / audio.rate) * 1e6), data });
      aenc.encode(chunk);
      chunk.close();
    }
    await aenc.flush();
    aenc.close();
    if (failed) throw failed;
  }
  muxer.finalize();
  const blob = new Blob([target.buffer], { type: "video/mp4" });
  // The poster: the chosen frame, at most 1280 on the long side.
  await videoSeek(v, opt.posterAt);
  const pk = Math.min(1, 1280 / Math.max(w, h)), pc = document.createElement("canvas");
  pc.width = Math.round(w * pk);
  pc.height = Math.round(h * pk);
  pc.getContext("2d").drawImage(v, 0, 0, pc.width, pc.height);
  const poster = await new Promise((ok, no) => pc.toBlob((b) => (b ? ok(b) : no(new Error("couldn't make the poster"))), "image/jpeg", 0.8));
  return { blob, poster, w, h, s: Math.round(Math.min(opt.len, (slot + 1) / VIDEO_FPS) * 10) / 10,
           mb: Math.round((blob.size / 1e6) * 10) / 10, sound: !!audio, frames };
}
const videoClock = (s) => Math.floor((s || 0) / 60) + ":" + String(Math.round(s || 0) % 60).padStart(2, "0");
// The video's panel in the spot editor (the mockup spot-video-2026-10-23, section 3): its preview, a strip of its
// frames with the kept part (30 s at most, moved with the slider) and the poster's frame (a click on the strip),
// Keep sound (off: kite clips are mostly wind, the owner 23.10.2026), what it becomes, then Convert and add.
async function videoConverter(box, file, code, added) {
  const url = URL.createObjectURL(file);
  box.innerHTML = '<div class="conv"><video id="i-cv" muted playsinline></video><div>' +
    "<b>" + esc(file.name) + '</b> <span class="dim" id="i-cstate">reading…</span><div class="dim" id="i-cfrom"></div>' +
    '<div class="cstrip" id="i-cstrip"><span class="csel" id="i-csel"></span><span class="cpost" id="i-cpost"></span></div>' +
    '<div class="ctimes"><span>0:00</span><span id="i-ckeep"></span><span id="i-cdur"></span></div>' +
    '<input type="range" id="i-cstart" min="0" step="0.1" value="0" style="width:100%;margin:2px 0 6px">' +
    '<label style="display:inline;font-size:13px;color:var(--ink)"><input type="checkbox" id="i-csound"> Keep sound</label> <span class="note">(off: kite clips are mostly wind) · click the strip to pick the poster frame</span>' +
    '<div class="mono" id="i-cnums" style="margin:6px 0"></div>' +
    '<div class="cbar"><i id="i-cbar"></i></div><div class="note" id="i-cmsg" style="margin:4px 0 8px">It plays through once while it converts: keep this tab in front.</div>' +
    '<button type="button" id="i-cgo">Convert and add</button> <button type="button" id="i-cx">Cancel</button></div></div>';
  const v = document.getElementById("i-cv"), $ = (id) => document.getElementById(id);
  const close = () => { box.innerHTML = ""; URL.revokeObjectURL(url); };
  $("i-cx").onclick = close;
  v.src = url;
  try { await videoEvent(v, "loadedmetadata"); } catch (e) { $("i-cstate").textContent = "can't be read: " + e.message; return; }
  const dur = v.duration, len = Math.min(30, dur), out = videoSize(v.videoWidth, v.videoHeight), rate = videoRate(out.w, out.h);
  let start = 0, posterAt = Math.min(dur, len / 2);
  $("i-cstate").textContent = "ready to convert";
  $("i-cfrom").textContent = "from the camera: " + v.videoWidth + "×" + v.videoHeight + " · " + videoClock(dur) + " · " + Math.round(file.size / 1e6) + " MB";
  $("i-cdur").textContent = videoClock(dur);
  const range = $("i-cstart");
  range.max = String(Math.max(0, dur - len));
  if (dur <= len) range.style.display = "none";
  const draw = () => {
    $("i-csel").style.left = (100 * start / dur) + "%";
    $("i-csel").style.width = (100 * len / dur) + "%";
    $("i-cpost").style.left = (100 * posterAt / dur) + "%";
    $("i-ckeep").textContent = "keep " + videoClock(start) + " → " + videoClock(start + len) + (dur > 30 ? " (30 s, the most)" : "") + " · poster at " + videoClock(posterAt);
    const mb = (rate + ($("i-csound").checked ? 128000 : 0)) * len / 8e6;
    $("i-cnums").textContent = "becomes " + out.w + "×" + out.h + " · 30 fps · H.264 · " + (rate / 1e6).toFixed(1) + " Mbit/s · ≈ " + Math.round(mb) + " MB (from " + Math.round(file.size / 1e6) + ")";
  };
  range.oninput = () => {
    start = +range.value;
    if (posterAt < start || posterAt > start + len) posterAt = start + len / 2;
    draw();
  };
  $("i-csound").onchange = draw;
  $("i-cstrip").onclick = (e) => {
    const r = $("i-cstrip").getBoundingClientRect();
    posterAt = Math.min(start + len - 0.1, Math.max(start, (dur * (e.clientX - r.left)) / r.width));
    draw();
  };
  draw();
  // The strip: 8 frames from a second copy of the video.
  (async () => {
    const s = document.createElement("video");
    s.muted = true;
    s.preload = "auto";
    s.src = url;
    try { await videoEvent(s, "loadeddata"); } catch (e) { return; }
    const c = document.createElement("canvas");
    c.height = 48;
    c.width = Math.round((48 * s.videoWidth) / s.videoHeight);
    for (let k = 0; k < 8; k++) {
      await videoSeek(s, ((k + 0.5) * dur) / 8);
      c.getContext("2d").drawImage(s, 0, 0, c.width, c.height);
      const img = document.createElement("img");
      img.src = c.toDataURL("image/jpeg", 0.7);
      $("i-cstrip") && $("i-cstrip").insertBefore(img, $("i-csel"));
    }
  })();
  $("i-cgo").onclick = async () => {
    $("i-cgo").disabled = true;
    $("i-cx").disabled = true;
    range.disabled = true;
    $("i-csound").disabled = true;
    $("i-cstate").textContent = "converting";
    try {
      // The time left from its pace so far (about the clip's length; slower when the browser hands frames over slowly).
      const began = Date.now();
      const made = await smallVideo(file, v, { start, len, sound: $("i-csound").checked, posterAt }, (p, msg) => {
        $("i-cbar").style.width = Math.round(p * 100) + "%";
        const left = p > 0.03 ? ((Date.now() - began) / 1000) * (1 - p) / p : (1 - p) * len;
        $("i-cmsg").textContent = msg || "converting in this browser · " + Math.round(p * 100) + "% · about " + Math.max(1, Math.round(left)) + " s left";
      });
      $("i-cmsg").textContent = "uploading " + made.mb + " MB…";
      const send = async (path, type, body) => {
        const reply = await (await fetch(path + "?code=" + encodeURIComponent(code), { method: "POST", headers: { "content-type": type, "x-shorewind-admin": "1" }, body })).json();
        if (!reply.ok) throw new Error(reply.error);
        return reply.url;
      };
      const poster = await send("/admin/api/spots/photo", "image/jpeg", made.poster);
      const video = await send("/admin/api/spots/video", "video/mp4", made.blob);
      added({ url: video, poster, w: made.w, h: made.h, s: made.s, mb: made.mb, ...(made.sound ? { sound: true } : {}) });
      close();
    } catch (err) {
      $("i-cstate").textContent = "not added";
      $("i-cmsg").textContent = "Not added: " + err.message;
      $("i-cx").disabled = false;
    }
  };
}
// A photo made smaller in the browser (at most "side" pixels a side), as a JPEG.
function smallerJpeg(file, side) {
  return new Promise((done, fail) => {
    const img = new Image();
    img.onload = () => {
      const k = Math.min(1, side / Math.max(img.naturalWidth, img.naturalHeight));
      const w = Math.round(img.naturalWidth * k), h = Math.round(img.naturalHeight * k);
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      canvas.getContext("2d").drawImage(img, 0, 0, w, h);
      canvas.toBlob((blob) => (blob ? done({ blob, w, h }) : fail(new Error("couldn't read the photo"))), "image/jpeg", 0.82);
      URL.revokeObjectURL(img.src);
    };
    img.onerror = () => fail(new Error("not an image the browser can read"));
    img.src = URL.createObjectURL(file);
  });
}
// The spot map in the editor (Leaflet, OpenStreetMap or satellite): pins and zones, and the
// amenities around from OpenStreetMap.
// The launch: the app's kite (front view), as on its spot bubbles.
const KITE_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" style="vertical-align:-4px;color:#0b8fa8"><g transform="rotate(10 12 12)"><path fill="currentColor" d="M1.8 15.4 C0.6 7.6 5.8 3.0 12.0 3.0 C18.2 3.0 23.4 7.6 22.2 15.4 L19.6 14.9 C20.0 9.6 16.6 7.0 12.0 7.0 C7.4 7.0 4.0 9.6 4.4 14.9 Z"/><path d="M3.1 15.2 L12 21.2 M20.9 15.2 L12 21.2 M7.4 8 L12 21.2 M16.6 8 L12 21.2" stroke="currentColor" stroke-width="1.1" fill="none" stroke-linecap="round"/></g></svg>';
const PIN_LOOK = { launch: [KITE_ICON, "Launch"], parking: ["🅿️", "Parking"], toilet: ["🚻", "Toilet"], shower: ["🚿", "Shower"], food: ["🍴", "Food"],
  school: ["🎓", "School"], rental: ["🏄", "Rental"], shop: ["🛍️", "Shop"], hazard: ["⚠️", "Hazard"], note: ["ℹ️", "Note"] };
const ZONE_LOOK = { nokite: ["#f85149", "No-kite zone"], swim: ["#d29922", "Swimming area"], kite: ["#3fb950", "Kite area"], noaccess: ["#e3b341", "Closed to walking"] };
let leaflet = null;
function loadLeaflet() {
  if (!leaflet) {
    leaflet = new Promise((done, fail) => {
      const css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
      document.head.appendChild(css);
      const js = document.createElement("script");
      js.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
      js.onload = () => done(window.L);
      js.onerror = () => fail(new Error("Leaflet didn't load"));
      document.head.appendChild(js);
    });
  }
  return leaflet;
}
async function spotMap(d, i, box) {
  const state = { pins: (i.pins || []).map((x) => ({ ...x })), zones: (i.zones || []).map((z) => ({ kind: z.kind, points: z.points.map((q) => q.slice()) })) };
  let L;
  try { L = await loadLeaflet(); } catch (e) { document.getElementById("i-mapnote").textContent = "The map didn't load: " + e.message; return state; }
  const map = L.map("i-map").setView([d.lat, d.lon], 15);
  const streets = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap contributors" });
  const satellite = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19, attribution: "Imagery © Esri" });
  satellite.addTo(map);
  L.control.layers({ Satellite: satellite, Map: streets }).addTo(map);
  // The spot's point: dragged, its coordinates go to the fields under the map (Move the spot here saves it).
  const point = L.marker([d.lat, d.lon], { draggable: true, zIndexOffset: 2000,
    icon: L.divIcon({ className: "", html: '<span style="display:block;width:14px;height:14px;border-radius:50%;background:#0AA2E6;border:2px solid #fff;box-shadow:0 0 3px #000"></span>',
                      iconSize: [18, 18], iconAnchor: [9, 9] }) }).addTo(map).bindTooltip("The spot's point (marker and forecast): drag to move it");
  point.on("dragend", () => {
    const at = point.getLatLng();
    document.getElementById("i-lat").value = at.lat.toFixed(5);
    document.getElementById("i-lon").value = at.lng.toFixed(5);
    document.getElementById("i-movenote").textContent = "Not saved yet: Move the spot here.";
  });
  for (const id of ["i-lat", "i-lon"]) {
    document.getElementById(id).oninput = () => {
      const a = +document.getElementById("i-lat").value, b = +document.getElementById("i-lon").value;
      if (Number.isFinite(a) && Number.isFinite(b)) point.setLatLng([a, b]);
    };
  }
  const layer = L.layerGroup().addTo(map);
  let tool = "launch", drawing = null;
  const tools = Object.entries(PIN_LOOK).map(([k, v]) => [k, v[0] + " " + esc(v[1])])
    .concat(Object.entries(ZONE_LOOK).map(([k, v]) => ["zone:" + k, "▱ " + esc(v[1])])).concat([["erase", "✕ Erase"], ["osm", "⤓ Fill from OpenStreetMap"]]);
  const bar = document.getElementById("i-tools");
  bar.innerHTML = tools.map((t) => '<button type="button" class="tick" data-tool="' + t[0] + '">' + t[1] + "</button>").join("") +
    '<button type="button" class="tick" id="i-finish" style="display:none">✓ Finish zone</button>';
  const note = (text) => { document.getElementById("i-mapnote").textContent = text; };
  const pick = (t) => {
    if (t === "osm") { fillFromOsm(); return; }
    tool = t;
    for (const b of bar.querySelectorAll("button[data-tool]")) b.classList.toggle("on", b.dataset.tool === t);
    selected = null;
    note(t === "erase" ? "Tap a pin or a zone to remove it." : t.startsWith("zone:") ? "Tap the corners of the zone, then Finish zone." :
      "Tap where the " + PIN_LOOK[t][1].toLowerCase() + " is. Tap a zone to change its shape.");
    draw();
  };
  // The zone being reshaped: its corners can be dragged, removed (tap) and added (tap a + between two).
  let selected = null;
  for (const b of bar.querySelectorAll("button[data-tool]")) b.onclick = () => pick(b.dataset.tool);
  const draw = () => {
    layer.clearLayers();
    state.zones.forEach((z, n) => {
      const color = ZONE_LOOK[z.kind][0];
      const shape = L.polygon(z.points, { color, weight: selected === n ? 3 : 2, fillOpacity: selected === n ? 0.28 : 0.18,
                                          dashArray: selected === n || z.kind === "noaccess" ? "6 4" : null }).addTo(layer).bindTooltip(ZONE_LOOK[z.kind][1]);
      shape.on("click", (e) => {
        if (drawing) return;
        L.DomEvent.stop(e);
        if (tool === "erase") { state.zones.splice(n, 1); selected = null; draw(); return; }
        selected = selected === n ? null : n;
        note(selected === null ? "" : "Reshaping the " + ZONE_LOOK[z.kind][1].toLowerCase() +
          ": drag a corner to move it, tap a corner to remove it, tap a + to add a corner there. Tap the map to finish.");
        draw();
      });
      if (selected !== n) return;
      const corner = (q) => L.divIcon({ className: "corner", html: "<span></span>", iconSize: [14, 14], iconAnchor: [7, 7] });
      z.points.forEach((q, i) => {
        const handle = L.marker(q, { icon: corner(), draggable: true, zIndexOffset: 1000 }).addTo(layer).bindTooltip("Drag to move, tap to remove");
        handle.on("drag", () => { const at = handle.getLatLng(); z.points[i] = [+at.lat.toFixed(5), +at.lng.toFixed(5)]; shape.setLatLngs(z.points); });
        handle.on("dragend", draw);
        handle.on("click", (e) => {
          L.DomEvent.stop(e);
          if (z.points.length <= 3) { note("A zone needs at least 3 corners: erase the whole zone instead."); return; }
          z.points.splice(i, 1);
          draw();
        });
      });
      z.points.forEach((q, i) => {
        const r = z.points[(i + 1) % z.points.length];
        const middle = [(q[0] + r[0]) / 2, (q[1] + r[1]) / 2];
        const plus = L.marker(middle, { icon: L.divIcon({ className: "corner add", html: "<span>+</span>", iconSize: [16, 16], iconAnchor: [8, 8] }),
                                        zIndexOffset: 900 }).addTo(layer).bindTooltip("Tap to add a corner here");
        plus.on("click", (e) => {
          L.DomEvent.stop(e);
          z.points.splice(i + 1, 0, [+middle[0].toFixed(5), +middle[1].toFixed(5)]);
          draw();
        });
      });
    });
    if (drawing) L.polyline(drawing.points, { color: ZONE_LOOK[drawing.kind][0], dashArray: "5 5" }).addTo(layer);
    state.pins.forEach((x, n) => {
      const icon = L.divIcon({ className: "pinicon", html: "<span>" + PIN_LOOK[x.kind][0] + "</span>", iconSize: [30, 30], iconAnchor: [15, 15] });
      const marker = L.marker([x.lat, x.lon], { icon, draggable: true }).addTo(layer)
        .bindTooltip(PIN_LOOK[x.kind][1] + (x.note ? ": " + x.note : ""));
      marker.on("dragend", () => { const at = marker.getLatLng(); x.lat = +at.lat.toFixed(5); x.lon = +at.lng.toFixed(5); });
      marker.on("click", () => {
        if (tool === "erase") { state.pins.splice(n, 1); draw(); return; }
        const text = prompt(PIN_LOOK[x.kind][1] + ": a short note (e.g. free, 200 m from the beach)", x.note || "");
        if (text !== null) { if (text.trim()) x.note = text.trim().slice(0, 120); else delete x.note; draw(); }
      });
    });
    document.getElementById("i-finish").style.display = drawing ? "" : "none";
  };
  map.on("click", (e) => {
    const at = [+e.latlng.lat.toFixed(5), +e.latlng.lng.toFixed(5)];
    // A tap outside the zone being reshaped finishes it (no pin there).
    if (selected !== null) { selected = null; note(""); draw(); return; }
    if (tool.startsWith("zone:")) {
      if (!drawing) drawing = { kind: tool.slice(5), points: [] };
      drawing.points.push(at);
      draw();
    } else if (PIN_LOOK[tool]) {
      state.pins.push({ kind: tool, lat: at[0], lon: at[1] });
      draw();
    }
  });
  document.getElementById("i-finish").onclick = () => {
    if (drawing && drawing.points.length >= 3) state.zones.push(drawing);
    drawing = null;
    draw();
  };
  // Parking, toilets, showers, food, shops and kite schools within a kilometre (OpenStreetMap):
  // the amenities ticked, the nearest of each kind pinned (check them, erase what's wrong).
  async function fillFromOsm() {
    note("Asking OpenStreetMap…");
    const at = "(around:1000," + d.lat + "," + d.lon + ")";
    const query = "[out:json][timeout:25];(nwr" + at + '[amenity~"^(parking|toilets|shower|restaurant|cafe|fast_food|bar|ice_cream)$"];' +
      "nwr" + at + "[shop];nwr(around:2000," + d.lat + "," + d.lon + ')[sport~"kitesurfing|windsurfing"];);out center 120;';
    let found;
    try {
      const reply = await fetch("https://overpass-api.de/api/interpreter", { method: "POST", body: "data=" + encodeURIComponent(query),
                                                                             headers: { "content-type": "application/x-www-form-urlencoded" } });
      found = (await reply.json()).elements || [];
    } catch (e) { note("OpenStreetMap didn't answer: " + e.message + " (try again in a minute)"); return; }
    const kindOf = (t) => {
      if (/kitesurfing|windsurfing/.test(t.sport || "")) return t.shop || /rental/.test(t.amenity || "") ? "rental" : "school";
      if (t.amenity === "parking") return "parking";
      if (t.amenity === "toilets") return "toilet";
      if (t.amenity === "shower") return "shower";
      if (/^(restaurant|cafe|fast_food|bar|ice_cream)$/.test(t.amenity || "")) return "food";
      if (t.shop) return "shop";
      return null;
    };
    const metres = (a, b) => Math.hypot((a[0] - b[0]) * 111320, (a[1] - b[1]) * 111320 * Math.cos(a[0] * Math.PI / 180));
    const byKind = {};
    for (const e of found) {
      const kind = kindOf(e.tags || {});
      const at2 = e.lat != null ? [e.lat, e.lon] : e.center ? [e.center.lat, e.center.lon] : null;
      if (!kind || !at2) continue;
      (byKind[kind] = byKind[kind] || []).push({ at: at2, name: (e.tags || {}).name || "", far: metres(at2, [d.lat, d.lon]) });
    }
    const added = [];
    for (const [kind, list] of Object.entries(byKind)) {
      list.sort((a, b) => a.far - b.far);
      // The nearest two (one shop), not where a pin of that kind is already.
      for (const item of list.slice(0, kind === "shop" ? 1 : 2)) {
        if (state.pins.some((x) => x.kind === kind && metres([x.lat, x.lon], item.at) < 60)) continue;
        state.pins.push({ kind, lat: +item.at[0].toFixed(5), lon: +item.at[1].toFixed(5), ...(item.name ? { note: item.name.slice(0, 120) } : {}) });
        added.push(PIN_LOOK[kind][1].toLowerCase());
      }
      const tick = box.querySelector('[data-group="amenities"] button[data-value="' + kind + '"]');
      if (tick) tick.classList.add("on");
    }
    draw();
    note(Object.keys(byKind).length ? "From OpenStreetMap: " + Object.entries(byKind).map(([k, v]) => v.length + " " + PIN_LOOK[k][1].toLowerCase()).join(", ") +
      " within 1 km. Pinned the nearest" + (added.length ? " (" + added.join(", ") + ")" : ": none new") + " and ticked the amenities: check them, erase what's wrong."
      : "OpenStreetMap has no parking, toilets, food or shops within 1 km here.");
  }
  pick("launch");
  draw();
  setTimeout(() => map.invalidateSize(), 50);
  return state;
}
// Riders' suggestions: before -> after for each change, the photos, Approve (the ticked parts) or Reject.
const SUGG_FIELDS = { about: "About", sports: "Sports", levels: "Level", water: "Water", standing: "Standing depth", standingNote: "Depth note",
                      beach: "Beach", bigLaunch: "Big launch", levelMatters: "Water level matters", tide: "Water level in the forecast", crowds: "Crowds", rules: "Rules", rule: "New rule", hazards: "Hazards",
                      hazardNote: "Hazard note", amenities: "Amenities", pins: "Map pins", zones: "Map zones", goodMonths: "Best months",
                      kiteable: "Possible directions", best: "Best directions" };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// A spot info field's value as text (a suggestion's before and after, a new spot's description).
function suggLabel(k, v) {
  if (v == null || v === "" || (Array.isArray(v) && !v.length)) return Array.isArray(v) ? "none" : "—";
  if (k === "kiteable" || k === "best") return v.map((p) => p[0] + "°→" + p[1] + "°").join(", ");
  if (k === "goodMonths") return v.map((m) => MONTHS[m - 1]).join(", ");
  if (k === "rules") return v.map((r) => r.text + (r.from && r.to ? " (" + r.from + " to " + r.to + ")" : "")).join("; ");
  if (k === "pins" || k === "zones") {
    const kinds = {};
    for (const x of v) kinds[x.kind] = (kinds[x.kind] || 0) + 1;
    return v.length + (k === "pins" ? " pins: " : " zones: ") + Object.entries(kinds).map(([kind, n]) => (n > 1 ? n + " " : "") + kind).join(", ") +
      (k === "pins" && v.some((x) => x.note) ? " · " + v.filter((x) => x.note).map((x) => "“" + x.note + "”").join(", ") : "");
  }
  return Array.isArray(v) ? v.join(", ") : v === true ? "yes" : v === false ? "no" : String(v);
}
// In the panel: drawn into "box"; hooks.count(n) the number waiting, hooks.changed() after a review, hooks.open(code) a spot.
async function loadSuggestions(box, hooks) {
  let d;
  try { d = await (await fetch("/admin/api/suggestions", { cache: "no-store" })).json(); } catch (e) { d = { error: String(e) }; }
  if (d.error) { box.textContent = "Can't load: " + d.error; return; }
  hooks.count(d.suggestions.length);
  box.innerHTML = d.suggestions.length ? d.suggestions.map((x) => {
    const face = x.face ? '<img src="' + esc(x.face) + '" class="face">' : '<span class="face"></span>';
    const rows = Object.keys(x.changes).filter((k) => SUGG_FIELDS[k]).map((k) =>
      '<label class="chg"><input type="checkbox" data-field="' + k + '" checked> <b>' + SUGG_FIELDS[k] + "</b> " +
      (k === "rule" ? "" : '<span class="dim">' + esc(suggLabel(k, x.current[k])) + "</span> → ") + esc(suggLabel(k, x.changes[k])) + "</label>").join("");
    const photos = x.photos.map((u) => '<label class="sph"><img src="' + esc(u) + '"><input type="checkbox" data-photo="' + esc(u) + '" checked></label>').join("");
    return '<div class="sugg" data-id="' + esc(x.id) + '"><div class="sh">' + face + "<div><b>" + esc(x.spot) + '</b> <span class="dim">· ' + esc(x.rider) +
      (x.nickname ? " (" + esc(x.nickname) + ")" : "") + " · " + new Date(x.created * 1000).toLocaleString() + "</span></div></div>" +
      (x.note ? '<div class="snote">“' + esc(x.note) + "”</div>" : "") + rows + (photos ? '<div class="sphs">' + photos + "</div>" : "") +
      '<div><button type="button" data-sa="approve" class="primary">✓ Approve ticked</button><button type="button" data-sa="reject">✕ Reject</button>' +
      (x.code ? '<button type="button" data-sedit="' + esc(x.code) + '">✎ Open the spot</button>' : "") + "</div></div>";
  }).join("") : '<div class="dim">No suggestions waiting.</div>';
  // Approved photos the spot lost (a later save from an editor opened before): on again with a click.
  if ((d.missing || []).length) box.innerHTML += '<div class="note" style="margin-top:12px">Approved photos not on their spot:</div>' + d.missing.map((x) =>
    '<div class="sugg" data-id="' + esc(x.id) + '"><div class="sh"><div><b>' + esc(x.spot) + '</b> <span class="dim">· ' + esc(x.rider) + " · approved " +
    new Date(x.reviewed * 1000).toLocaleString() + "</span></div></div>" + '<div class="sphs">' +
    x.photos.map((u) => '<label class="sph"><img src="' + esc(u) + '"><input type="checkbox" data-photo="' + esc(u) + '" checked></label>').join("") +
    '</div><div><button type="button" data-reapply="1" class="primary">↺ Add ticked to the spot again</button></div></div>').join("");
  for (const b of box.querySelectorAll("button[data-reapply]")) {
    b.onclick = async () => {
      const card = b.closest(".sugg");
      const photos = [...card.querySelectorAll("input[data-photo]:checked")].map((c) => c.dataset.photo);
      const r = await fetch("/admin/api/suggestions", { method: "POST", headers: { "content-type": "application/json", "x-shorewind-admin": "1" },
                                                  body: JSON.stringify({ id: card.dataset.id, action: "reapply", photos }) });
      const reply = await r.json();
      if (!reply.ok) alert("Not done: " + reply.error);
      else alert(reply.added + " photo" + (reply.added === 1 ? "" : "s") + " back on the spot" + (reply.draft ? " (in its open draft: publish it)" : ""));
      loadSuggestions(box, hooks);
      hooks.changed();
    };
  }
  for (const b of box.querySelectorAll("button[data-sa]")) {
    b.onclick = async () => {
      const card = b.closest(".sugg");
      const fields = [...card.querySelectorAll("input[data-field]:checked")].map((c) => c.dataset.field);
      const photos = [...card.querySelectorAll("input[data-photo]:checked")].map((c) => c.dataset.photo);
      if (b.dataset.sa === "reject" && !confirm("Reject this suggestion? Its photos are deleted.")) return;
      const r = await fetch("/admin/api/suggestions", { method: "POST", headers: { "content-type": "application/json", "x-shorewind-admin": "1" },
                                                  body: JSON.stringify({ id: card.dataset.id, action: b.dataset.sa, fields, photos }) });
      const reply = await r.json();
      if (!reply.ok) alert("Not done: " + reply.error);
      loadSuggestions(box, hooks);
      hooks.changed();
    };
  }
  for (const b of box.querySelectorAll("button[data-sedit]")) b.onclick = () => hooks.open(b.dataset.sedit);
}
return { openInfo, loadSuggestions, described };
})();

// A box Preact draws once and leaves alone: the old code owns what's inside (the panel draws again every second).
// A new key (another spot) makes a new box.
class Mount extends Component {
  shouldComponentUpdate() { return false; }
  componentDidMount() { this.props.run(this.base); }
  render() { return html`<div></div>`; }
}

function SpotEditBox({ code, onClose, onSaved }) {
  return html`<div class="panel oldspot"><${Mount} key=${code} run=${(box) => OldSpots.openInfo(code, box, { close: onClose, saved: onSaved })} /></div>`;
}

const osmLink = (o) => `https://www.openstreetmap.org/?mlat=${(+o.lat).toFixed(5)}&mlon=${(+o.lon).toFixed(5)}#map=14/${(+o.lat).toFixed(5)}/${(+o.lon).toFixed(5)}`;

// Spot sets (the owner, 30.10.2026: "in spots I need to have possibility to define set, now not possible (or its should
// suggest set automatically after new spot added)"). A public rider spot gets the set whose spots are nearest (within
// 150 km), else a new set named from its country (the spot's time zone) and sea; nothing joins a set without a click.
const kmBetween = (a, b) => {
  const r = Math.PI / 180, dLat = (b.lat - a.lat) * r, dLon = (b.lon - a.lon) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLon / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
};
// Time zone -> [country code, name]; a zone not here leaves both for the owner to type.
const ZONE_COUNTRY = Object.fromEntries(Object.entries({
  LV: ["Latvia", "Europe/Riga"], EE: ["Estonia", "Europe/Tallinn"], LT: ["Lithuania", "Europe/Vilnius"], FI: ["Finland", "Europe/Helsinki"],
  SE: ["Sweden", "Europe/Stockholm"], NO: ["Norway", "Europe/Oslo"], DK: ["Denmark", "Europe/Copenhagen"], DE: ["Germany", "Europe/Berlin Europe/Busingen"],
  PL: ["Poland", "Europe/Warsaw"], NL: ["Netherlands", "Europe/Amsterdam"], BE: ["Belgium", "Europe/Brussels"], FR: ["France", "Europe/Paris"],
  GB: ["United Kingdom", "Europe/London"], IE: ["Ireland", "Europe/Dublin"], PT: ["Portugal", "Europe/Lisbon Atlantic/Madeira Atlantic/Azores"],
  ES: ["Spain", "Europe/Madrid Atlantic/Canary Africa/Ceuta"], IT: ["Italy", "Europe/Rome"], GR: ["Greece", "Europe/Athens"], TR: ["Türkiye", "Europe/Istanbul"],
  HR: ["Croatia", "Europe/Zagreb"], SI: ["Slovenia", "Europe/Ljubljana"], ME: ["Montenegro", "Europe/Podgorica"], AL: ["Albania", "Europe/Tirane"],
  BG: ["Bulgaria", "Europe/Sofia"], RO: ["Romania", "Europe/Bucharest"], UA: ["Ukraine", "Europe/Kyiv Europe/Kiev"], MT: ["Malta", "Europe/Malta"],
  MC: ["Monaco", "Europe/Monaco"], GI: ["Gibraltar", "Europe/Gibraltar"], AT: ["Austria", "Europe/Vienna"], CH: ["Switzerland", "Europe/Zurich"],
  CZ: ["Czechia", "Europe/Prague"], HU: ["Hungary", "Europe/Budapest"], SK: ["Slovakia", "Europe/Bratislava"], RS: ["Serbia", "Europe/Belgrade"],
  LU: ["Luxembourg", "Europe/Luxembourg"], IS: ["Iceland", "Atlantic/Reykjavik"], FO: ["Faroe Islands", "Atlantic/Faroe"], CV: ["Cape Verde", "Atlantic/Cape_Verde"],
  CY: ["Cyprus", "Asia/Nicosia Europe/Nicosia Asia/Famagusta"], MA: ["Morocco", "Africa/Casablanca"], EG: ["Egypt", "Africa/Cairo"], TN: ["Tunisia", "Africa/Tunis"],
  ZA: ["South Africa", "Africa/Johannesburg"], SN: ["Senegal", "Africa/Dakar"], KE: ["Kenya", "Africa/Nairobi"], TZ: ["Tanzania", "Africa/Dar_es_Salaam"],
  MU: ["Mauritius", "Indian/Mauritius"], MV: ["Maldives", "Indian/Maldives"], IL: ["Israel", "Asia/Jerusalem"], AE: ["United Arab Emirates", "Asia/Dubai"],
  OM: ["Oman", "Asia/Muscat"], LK: ["Sri Lanka", "Asia/Colombo"], VN: ["Vietnam", "Asia/Ho_Chi_Minh"], TH: ["Thailand", "Asia/Bangkok"],
  PH: ["Philippines", "Asia/Manila"], ID: ["Indonesia", "Asia/Makassar Asia/Jakarta"], BR: ["Brazil", "America/Fortaleza America/Recife America/Bahia America/Sao_Paulo America/Belem"],
  MX: ["Mexico", "America/Mexico_City America/Cancun America/Mazatlan America/Tijuana"], DO: ["Dominican Republic", "America/Santo_Domingo"],
  CW: ["Curaçao", "America/Curacao"], AW: ["Aruba", "America/Aruba"], PR: ["Puerto Rico", "America/Puerto_Rico"], PE: ["Peru", "America/Lima"],
  CL: ["Chile", "America/Santiago"], CO: ["Colombia", "America/Bogota"], US: ["United States", "America/New_York America/Chicago America/Denver America/Los_Angeles Pacific/Honolulu"],
  AU: ["Australia", "Australia/Perth Australia/Sydney Australia/Brisbane Australia/Melbourne Australia/Adelaide"], NZ: ["New Zealand", "Pacific/Auckland"],
}).flatMap(([code, [name, zones]]) => zones.split(" ").map((z) => [z, [code, name]])));
// The sea by rough boxes, first match (the owner edits the name anyway).
const seaOf = (lat, lon) => lat > 53.5 && lat < 66 && lon > 9.5 && lon < 30.5 ? "Baltic"
  : lat > 50.8 && lat < 61 && lon > -4 && lon < 9.5 ? "North Sea"
  : lat > 40.8 && lat < 47 && lon > 27.3 && lon < 42 ? "Black Sea"
  : lat > 12 && lat < 30 && lon > 32 && lon < 44 ? "Red Sea"
  : lat > 43 && lat < 48.5 && lon > -10 && lon < -1 ? "Atlantic"
  : lat > 30 && lat < 46 && lon > -5.6 && lon < 36.5 ? "Mediterranean"
  : lat > 20 && lat < 60 && lon > -30 && lon < -1 ? "Atlantic" : "";
const flagLetters = (flag) => [...String(flag || "")].map((c) => c.codePointAt(0)).every((n) => n >= 0x1f1e6 && n <= 0x1f1ff)
  ? [...flag].map((c) => String.fromCharCode(c.codePointAt(0) - 0x1f1e6 + 65)).join("") : String(flag || "");
const setLabel = (st) => html`<span class="flagcode">${flagLetters(st.flag) || "–"}</span>${st.name}`;
const zoneCache = {};
async function zoneOf(p) {
  const k = (+p.lat).toFixed(3) + "," + (+p.lon).toFixed(3);
  if (!(k in zoneCache)) zoneCache[k] = fetch(`https://data.meteogust.com/timezone?lat=${(+p.lat).toFixed(4)}&lon=${(+p.lon).toFixed(4)}`)
    .then((r) => r.json()).then((z) => typeof z.timezone === "string" ? z.timezone : "").catch(() => "");
  return zoneCache[k];
}

// The sets with their distance from this spot (its nearest spot in each), nearest first.
function setsNear(p, sets, official) {
  return sets.map((st) => {
    const inSet = official.filter((o) => (o.sets || []).includes(st.id));
    const near = inSet.reduce((best, o) => { const km = kmBetween(p, o); return !best || km < best.km ? { km, spot: o.name } : best; }, null);
    return { ...st, km: near ? near.km : Infinity, spot: near ? near.spot : "" };
  }).sort((a, b) => a.km - b.km);
}

function PublicSpotRow({ p, sets, official, links, refresh }) {
  const ranked = setsNear(p, sets, official);
  const near = ranked[0] && ranked[0].km <= 150 ? ranked[0] : null;
  const [pick, setPick] = useState(near ? near.id : "");
  const [form, setForm] = useState(null);
  const [zone, setZone] = useState(null);
  const [msg, setMsg] = useState("");
  useEffect(() => { if (!near) zoneOf(p).then(setZone); }, [p.code]);
  const [cc, country] = (zone && ZONE_COUNTRY[zone]) || ["", ""];
  const sea = seaOf(+p.lat, +p.lon);
  const guess = { name: [country, sea].filter(Boolean).join(" · "), flag: cc, after: sets.length ? sets[sets.length - 1].id : "" };
  const official_ = async (set) => { const d = await adminPost("spots", { code: p.code, action: "official", set }); refresh(); return d; };
  const create = async () => {
    const { data } = await adminPostRaw("spots", { action: "set-new", name: form.name, flag: form.flag, after: form.after });
    if (data.error) { setMsg(data.error); return; }
    const { data: d2 } = await adminPostRaw("spots", { code: p.code, action: "official", set: data.id });
    if (d2.error) { setMsg(d2.error); return; }
    setForm(null); refresh();
  };
  return html`<div class="prow">
    <div><b>${p.name}</b><div class="whytxt">by ${p.author || "a rider"} · ${links(p)}${zone ? " · " + zone : ""}</div></div>
    <div class="suggest">
      ${near ? html`<span class="suglab">Suggested: <b>${setLabel(near)}</b> · its nearest spot ${Math.round(near.km)} km</span>`
        : html`<span class="suglab">No set within 150 km${guess.name ? html` · a new one: <b><span class="flagcode">${guess.flag || "–"}</span>${guess.name}</b>` : ""}</span>`}
      <select value=${form ? "+new" : pick} onChange=${(e) => { if (e.target.value === "+new") setForm(guess); else { setForm(null); setPick(e.target.value); } }}>
        <option value="">${near ? "other set…" : "set…"}</option>
        ${ranked.map((st) => html`<option value=${st.id}>${flagLetters(st.flag)} ${st.name}${Number.isFinite(st.km) ? ` · ${Math.round(st.km).toLocaleString("en")} km` : ""}</option>`)}
        <option value="+new">+ New set…</option></select>
      ${form ? null : pick ? html`<${ActButton} label="Make official" primary=${true} run=${() => official_(pick)} />`
        : html`<button class="btn primary" onClick=${() => setForm(guess)}>New set…</button>`}
      <${ActButton} label="Unpublish" danger=${true} run=${async () => { const d = await adminPost("spots", { code: p.code, action: "unpublish" }); refresh(); return d; }} />
    </div>
    ${form ? html`<div class="setform">
      <label>Name<input maxlength="60" value=${form.name} onInput=${(e) => setForm({ ...form, name: e.target.value })} placeholder="Country · sea"/></label>
      <label>Flag<input maxlength="8" value=${form.flag} onInput=${(e) => setForm({ ...form, flag: e.target.value })} placeholder="FR"/></label>
      <label>Place in the list<select value=${form.after} onChange=${(e) => setForm({ ...form, after: e.target.value })}>
        <option value="">first</option>${sets.map((st) => html`<option value=${st.id}>after ${st.name}</option>`)}</select></label>
      <span class="btns"><button class="btn primary" disabled=${!form.name.trim()} onClick=${create}>Create and make official</button><button class="btn" onClick=${() => setForm(null)}>Cancel</button></span>
      <div class="whytxt full">${zone ? `The flag from the spot's time zone (${zone}${cc ? " → " + cc : ", not in the panel's list"}), the sea from where it is.` : "Looking up the spot's time zone…"}
        Two letters make the flag. New riders are offered the set at first launch from their next app start.${msg ? html` <b style="color:var(--bad)">${msg}</b>` : null}</div>
    </div>` : null}
  </div>`;
}

function SetsPanel({ spots, refresh, riders }) {
  const sets = spots.sets || [];
  const official = (spots.official || []).filter((o) => !String(o.spot_id).startsWith("w-"));
  const [edit, setEdit] = useState(null);
  const [msg, setMsg] = useState("");
  const post = async (body) => { const { data } = await adminPostRaw("spots", body); setMsg(data.error ? data.error : "✓ saved"); if (!data.error) refresh(); return data; };
  const move = (i, by) => { const ids = sets.map((s) => s.id); const [x] = ids.splice(i, 1); ids.splice(i + by, 0, x); post({ action: "set-order", ids }); };
  // Phones that chose each set at first launch (sent by builds from 30.10.2026).
  const chose = {};
  for (const d of (riders && riders.devices) || []) for (const id of String(d.sets || "").split(",").filter(Boolean)) chose[id] = (chose[id] || 0) + 1;
  const anyChoice = Object.keys(chose).length > 0;
  return html`<div class="panel s12"><h2>Spot sets · ${sets.length}</h2><div class="tablewrap"><table class="sets"><thead><tr><th></th><th>Set</th><th class="r">Spots</th><th class="hide-s">In it</th><th class="r">Riders who chose it</th><th></th></tr></thead><tbody>
    ${sets.map((st, i) => {
      const list = official.filter((o) => (o.sets || []).includes(st.id));
      const builtIn = list.filter((o) => (o.sets || []).length === 1 && !String(o.spot_id).startsWith("rider-")).length;
      return html`<tr><td class="order"><button class="btn mini" disabled=${i === 0} onClick=${() => move(i, -1)} title="up">↑</button><button class="btn mini" disabled=${i === sets.length - 1} onClick=${() => move(i, 1)} title="down">↓</button></td>
        <td>${edit && edit.id === st.id ? html`<span class="btns"><input style="width:56px" maxlength="8" value=${edit.flag} onInput=${(e) => setEdit({ ...edit, flag: e.target.value })}/>
            <input maxlength="60" value=${edit.name} onInput=${(e) => setEdit({ ...edit, name: e.target.value })}/></span>` : html`<b>${setLabel(st)}</b>`}</td>
        <td class="r num">${list.length}</td><td class="whytxt hide-s">${list.slice(0, 4).map((o) => o.name).join(", ")}${list.length > 4 ? "…" : ""}</td>
        <td class="r num">${anyChoice ? chose[st.id] || 0 : "–"}</td>
        <td class="r"><span class="btns" style="justify-content:flex-end">${edit && edit.id === st.id
          ? html`<button class="btn primary" onClick=${async () => { const d = await post({ action: "set-edit", set: st.id, name: edit.name, flag: edit.flag }); if (!d.error) setEdit(null); }}>Save</button><button class="btn" onClick=${() => setEdit(null)}>Cancel</button>`
          : html`<button class="btn" onClick=${() => setEdit({ id: st.id, name: st.name, flag: flagLetters(st.flag) })}>Rename</button>
            ${builtIn ? null : html`<${ActButton} label="Remove" danger=${true} ask=${list.length ? `Remove? Its ${list.length} spot${list.length > 1 ? "s go" : " goes"} back to Public rider spots. Click again` : "Remove? Click again"}
              run=${() => post({ action: "set-remove", set: st.id })} />`}`}</span></td></tr>`;
    })}</tbody></table></div>
    <p class="whytxt" style="margin:10px 0 0">↑ ↓ change the order new riders see at first launch; a change reaches phones within a few hours, no build needed. A set with built-in spots can't be removed until they're moved to another set (their set menu below); riders' spots go back to Public rider spots.
      Riders who chose it: phones on builds from 30.10.2026 that picked the set at first launch.${msg ? html` <${Saved} msg=${msg} />` : null}</p></div>`;
}

function SpotsEditor({ spots, roles, refresh, now }) {
  const [edit, setEdit] = useState(null);
  const [waiting, setWaiting] = useState(null);
  if (!spots) return html`<p class="empty">Reading the spots…</p>`;
  if (edit) return html`<${SpotEditBox} code=${edit} onClose=${() => { setEdit(null); refresh(); }} onSaved=${refresh} />`;
  const sets = spots.sets || [];
  const official = (spots.official || []).filter((o) => !String(o.spot_id).startsWith("w-"));
  // A spot under each set it's in (as on the old page), the rest under "In no set".
  const groups = [...sets.map((st) => [st, official.filter((o) => (o.sets || []).includes(st.id))]),
    [{ id: "", name: "In no set", flag: "" }, official.filter((o) => !(o.sets || []).some((x) => sets.some((st) => st.id === x)))]];
  const act = async (body) => { const { data } = await adminPostRaw("spots", body); if (data.error) alertMsg(data.error); refresh(); };
  const riderLinks = (p) => html`<a href=${osmLink(p)} target="_blank" style="color:var(--accent)">${(+p.lat).toFixed(3)}, ${(+p.lon).toFixed(3)}</a> ·
    <a href=${spots.base + "/s/" + p.code} target="_blank" style="color:var(--accent)">rider link</a>`;
  return html`<div class="grid">
    <div class="panel s12 oldspot"><h2>Suggestions from riders${waiting ? ` · ${waiting} waiting` : ""}</h2>
      <${Mount} key="suggestions" run=${(box) => OldSpots.loadSuggestions(box, { count: setWaiting, changed: refresh, open: setEdit })} />
      <p class="note">Riders signed in suggest changes to a spot's info in the app. Tick what to take: Approve merges it into the spot's info (its draft when it has one, else straight to the app) with the rider's nickname on the photos. The note isn't copied: put what's useful in About yourself.</p></div>
    <div class="panel s12 oldspot"><h2>Riders' spots waiting for review · ${(spots.pending || []).length}</h2>
      ${(spots.pending || []).length ? html`<div class="tablewrap"><table><tbody>
      ${spots.pending.map((p) => html`<tr><td><b>${p.name}</b><div class="whytxt">by ${p.author || "a rider"} · ${p.updated ? hrs(now - p.updated) + " ago · " : ""}${riderLinks(p)}</div>
        ${p.described ? html`<div class="described" dangerouslySetInnerHTML=${{ __html: OldSpots.described(p) }}></div>` : null}</td>
        <td class="r"><span class="btns"><${ActButton} label="Approve" primary=${true} run=${async () => { const d = await adminPost("spots", { code: p.code, action: "approve" }); refresh(); return d; }} />
          <${ActButton} label="Reject" ask="Reject? Its link stops working. Click again" danger=${true} run=${async () => { const d = await adminPost("spots", { code: p.code, action: "reject" }); refresh(); return d; }} /></span></td></tr>`)}</tbody></table></div>`
        : html`<p class="whytxt">Nothing waiting.</p>`}
      <p class="note">A rider suggested these as public. Approve: everyone finds them in Add spots. Reject: they stay private and their link stops working.</p></div>
    <div class="panel s12"><h2>Public rider spots · ${(spots.published || []).length}</h2>
      ${(spots.published || []).map((p) => html`<${PublicSpotRow} key=${p.code} p=${p} sets=${sets} official=${official} links=${riderLinks} refresh=${refresh} />`)}
      ${!(spots.published || []).length ? html`<p class="whytxt">None waiting for a set.</p>` : null}
      <p class="note">Make official: the spot joins the set as a MeteoGust spot (offered on first launch). The suggestion is the set whose spots are nearest, or a new set when none is within 150 km.</p></div>
    <${SetsPanel} spots=${spots} refresh=${refresh} riders=${roles} />
    ${groups.filter(([, list]) => list.length).map(([st, list]) => html`<div class="panel s12"><h2>${st.flag} ${st.name} · ${list.length}</h2><div class="tablewrap"><table><tbody>
      ${list.slice().sort((a, b) => a.position - b.position).map((o) => html`<tr><td><b>${o.name}</b>${o.hidden ? html` <span class="st unknown">hidden</span>` : null}<div class="whytxt">${o.spot_id} · info ${o.score}/13${o.draft ? " · draft" : ""} · <a href=${osmLink(o)} target="_blank" style="color:var(--accent)">map</a></div></td>
        <td class="r"><span class="btns"><button class="btn primary" onClick=${() => setEdit(o.code)}>Edit</button>
          <select value=${(o.sets || [])[0] || ""} onChange=${(e) => e.target.value && act({ code: o.code, action: "set", set: e.target.value })}>${!(o.sets || []).length ? html`<option value="">set…</option>` : null}${sets.map((x) => html`<option value=${x.id}>${x.flag} ${x.name}</option>`)}</select>
          <${ActButton} label=${o.hidden ? "Show" : "Hide"} ask=${o.hidden ? null : "Hide? Riders who have it keep it. Click again"} run=${() => act({ code: o.code, action: o.hidden ? "show" : "hide" })} /></span></td></tr>`)}</tbody></table></div></div>`)}
    ${!official.length ? html`<div class="panel s12"><${ActButton} label=${"Import the " + spots.builtIn + " built-in spots"} run=${async () => { const d = await adminPost("spots", { action: "import" }); refresh(); return d; }} /></div>` : null}
    <p class="whytxt s12" style="margin:0">What the app offers: on first launch by set, and in Add spots. A set change or a hidden spot reaches phones within a few hours, without a new build; a hidden spot keeps working for riders who have it. · ${spots.shared ?? "–"} spots shared by link only · ${spots.rejected ?? "–"} rejected</p>
  </div>`;
}

let alertMsg = (m) => alert("Not done: " + m);

function WeightPoints({ spots, refresh }) {
  const [edit, setEdit] = useState(null);
  const [form, setForm] = useState({ name: "", lat: "", lon: "", weights: {}, source: "" });
  const [msg, setMsg] = useState("");
  if (!spots) return html`<p class="empty">Reading the spots…</p>`;
  if (edit) return html`<${SpotEditBox} code=${edit} onClose=${() => { setEdit(null); refresh(); }} onSaved=${refresh} />`;
  const points = (spots.official || []).filter((o) => String(o.spot_id).startsWith("w-"));
  const add = async () => {
    const { data } = await adminPostRaw("spots", { action: "point", name: form.name, lat: form.lat, lon: form.lon, weights: form.weights, source: form.source });
    setMsg(data.error ? data.error : "✓ added");
    if (!data.error) { setForm({ name: "", lat: "", lon: "", weights: {}, source: "" }); refresh(); }
  };
  return html`<div class="grid">
    <div class="panel s6"><h2>Weight points · ${points.length}</h2><p class="note">Places with their own model weights, not shown as spots: the Mix and new spots near them use these weights.</p>
      <div class="tablewrap"><table><tbody>${points.map((o) => html`<tr><td><b>${o.name}</b><div class="whytxt">${(+o.lat).toFixed(3)}, ${(+o.lon).toFixed(3)}</div></td>
        <td class="r"><button class="btn" onClick=${() => setEdit(o.code)}>Edit weights, move</button></td></tr>`)}</tbody></table></div></div>
    <div class="panel s6"><h2>Add a weight point</h2><div class="form">
      <label>Name<input maxlength="60" value=${form.name} onInput=${(e) => setForm({ ...form, name: e.target.value })}/></label>
      <label>Where the weights come from<input maxlength="80" value=${form.source} onInput=${(e) => setForm({ ...form, source: e.target.value })}/></label>
      <label>Latitude<input value=${form.lat} onInput=${(e) => setForm({ ...form, lat: e.target.value })}/></label>
      <label>Longitude<input value=${form.lon} onInput=${(e) => setForm({ ...form, lon: e.target.value })}/></label></div>
      <div style="margin-top:10px"><${WeightsGrid} value=${form.weights} onChange=${(w) => setForm({ ...form, weights: w })} /></div>
      <div class="btns" style="margin-top:10px"><button class="btn primary" onClick=${add} disabled=${!form.name || !form.lat || !form.lon}>Add</button><${Saved} msg=${msg} /></div></div>
  </div>`;
}

