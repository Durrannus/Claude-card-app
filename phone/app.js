/* Card Logger for iPhone: the screens. All the card logic is the desktop
   app's own Python code (card_logger/), run in the browser by Pyodide and
   reached through card_logger/phone_api.py. Your data is kept on the phone
   (IndexedDB); the day's prices and decklists come from data/market.db. */
"use strict";

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
const HOME = "/home/pyodide";
const USER_DB = HOME + "/user.db";
const WRITES = new Set(["open_db", "set_currency", "set_ebay_site", "save_card", "delete_cards", "move_cards",
  "update_prices", "import_csv", "add_deck", "delete_decks", "add_missing", "catalog_wishlist", "sold_add",
  "sold_delete", "wishlist_names"]);

let py = null, callPy = null, info = {}, dataInfo = null, dirty = false;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const h = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const opts = (list, current) => list.map((o) => {
  const [value, label] = Array.isArray(o) ? o : [o, o];
  return `<option value="${h(value)}"${String(value) === String(current) ? " selected" : ""}>${h(label)}</option>`;
}).join("");
const tick = () => new Promise((r) => setTimeout(r, 30));

const state = {
  tab: localStorage.getItem("tab") || "market",
  collection: { view: "collection", search: "", game: "", sort: "", reverse: false, select: false, selected: new Set() },
  meta: { legend: "All legends", since: "", top: "All placements", runes: false, section: "cards", select: false, selected: new Set() },
  market: { view: "catalog", days: 7, search: "", set: "", rarity: "", version: "", metaFilter: "Any meta", min1: false,
    sort: "price", reverse: true, limit: 150, filters: false, signal: "All signals", ssort: "", sreverse: true },
  insight: { weeks: "Last 6 weeks", view: "all" },
};

/* --- storage ---------------------------------------------------------------- */

let dbPromise = null;
function idb() {
  dbPromise = dbPromise || new Promise((resolve, reject) => {
    const r = indexedDB.open("card-logger", 1);
    r.onupgradeneeded = () => { r.result.createObjectStore("files"); r.result.createObjectStore("photos"); };
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
  return dbPromise;
}
async function idbDo(store, mode, fn) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const req = fn(t.objectStore(store));
    t.oncomplete = () => resolve(req && req.result);
    t.onerror = () => reject(t.error);
  });
}
const idbGet = (store, key) => idbDo(store, "readonly", (s) => s.get(key));
const idbPut = (store, key, value) => idbDo(store, "readwrite", (s) => s.put(value, key));
const idbDel = (store, key) => idbDo(store, "readwrite", (s) => s.delete(key));

async function persist() {
  if (!dirty) return;
  dirty = false;
  await idbPut("files", "user.db", py.FS.readFile(USER_DB));
}

/* --- Python ------------------------------------------------------------------ */

function api(name, args = {}) {
  const out = JSON.parse(callPy(name, JSON.stringify(args)));
  if ("error" in out) throw new Error(out.error);
  if (WRITES.has(name)) dirty = true;
  return out.ok;
}

/* Run a slower call with a "working" overlay (Python runs on the page's
   thread, so the overlay has to be drawn before it starts). */
async function work(message, fn) {
  const el = document.createElement("div");
  el.className = "busy";
  el.innerHTML = `<div class="box"><div class="spinner"></div><div>${h(message)}</div></div>`;
  document.body.appendChild(el);
  await tick();
  try {
    return await fn();
  } catch (e) {
    toast(e.message, true);
    return undefined;
  } finally {
    el.remove();
    await persist();
  }
}

function safe(fn) {
  try { return fn(); } catch (e) { toast(e.message, true); return undefined; } finally { persist(); }
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src; s.onload = resolve; s.onerror = () => reject(new Error("Couldn't load " + src));
    document.head.appendChild(s);
  });
}

async function boot() {
  const say = (m) => { $("#loading-msg").textContent = m; };
  try {
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
    if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
    say("Loading Python… (the first start downloads about 10 MB)");
    await loadScript(PYODIDE + "pyodide.js");
    py = await loadPyodide({ indexURL: PYODIDE });
    await py.loadPackage("sqlite3");
    say("Loading the app…");
    const files = await (await fetch("py/files.json", { cache: "no-cache" })).json();
    py.FS.mkdirTree(HOME + "/card_logger");
    await Promise.all(files.map(async (f) => {
      const text = await (await fetch("py/" + f, { cache: "no-cache" })).text();
      py.FS.writeFile(HOME + "/" + f, text);
    }));
    const saved = await idbGet("files", "user.db");
    if (saved) py.FS.writeFile(USER_DB, saved);

    say("Getting today's prices and decklists…");
    let marketPath = "", built = "";
    try {
      dataInfo = await (await fetch("data/info.json", { cache: "no-cache" })).json();
      built = dataInfo.built;
      if (!saved || built !== localStorage.getItem("market_built")) {
        const buf = new Uint8Array(await (await fetch("data/market.db", { cache: "no-cache" })).arrayBuffer());
        py.FS.writeFile(HOME + "/market.db", buf);
        marketPath = HOME + "/market.db";
      }
    } catch (e) {
      dataInfo = null; // offline: use what the phone already has
    }
    py.runPython(`import sys\nsys.path.insert(0, "${HOME}")`);
    callPy = py.pyimport("card_logger.phone_api").call;
    say("Opening your collection…");
    await tick();
    info = api("open_db", { path: USER_DB, market_path: marketPath, built });
    await persist();
    if (marketPath) localStorage.setItem("market_built", built);
    $("#loading").remove();
    if (!dataInfo) toast("Offline: showing the prices and decklists from your last visit.");
    show(state.tab);
  } catch (e) {
    say("Couldn't start: " + e.message + ". Check your internet connection and reopen the app.");
    $(".spinner")?.remove();
    console.error(e);
  }
}

/* --- small UI helpers ------------------------------------------------------ */

let toastTimer = null;
function toast(message, error = false) {
  $(".toast")?.remove();
  const el = document.createElement("div");
  el.className = "toast" + (error ? " err" : "");
  el.textContent = message;
  el.onclick = () => el.remove();
  document.body.appendChild(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), error ? 6000 : 3500);
}

function sheet(title, body, onClose) {
  const back = document.createElement("div");
  back.className = "sheet-back";
  back.innerHTML = `<div class="sheet" role="dialog" aria-label="${h(title)}">
    <div class="sheet-head"><h3>${h(title)}</h3><button class="btn small" data-close>Done</button></div>
    <div class="sheet-body">${body}</div></div>`;
  const close = () => { back.remove(); if (onClose) onClose(); };
  back.addEventListener("click", (e) => { if (e.target === back) close(); });
  $("[data-close]", back).onclick = close;
  document.body.appendChild(back);
  back.close = close;
  return back;
}

function chart(points, fmt, zero = false, empty = "Not enough history yet.") {
  const pts = (points || []).filter((p) => p[1] !== null && p[1] !== undefined);
  if (pts.length < 2) return `<div class="note" style="padding:18px 4px">${h(empty)}</div>`;
  const W = 330, H = 130, L = 46, R = 58, T = 12, B = 22;
  const vals = pts.map((p) => p[1]);
  let lo = zero ? 0 : Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 1e-9) { hi += 1; lo = zero ? 0 : lo - 1; }
  const all = points || [];
  const x = (i) => L + (all.length > 1 ? (i / (all.length - 1)) * (W - L - R) : 0);
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  let path = "", pen = false;
  all.forEach((p, i) => {
    if (p[1] === null || p[1] === undefined) { pen = false; return; }
    path += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)} `;
    pen = true;
  });
  const lastIndex = all.map((p) => p[1] !== null && p[1] !== undefined).lastIndexOf(true);
  const last = all[lastIndex];
  const color = zero ? "var(--play)" : "var(--price)";
  const day = (d) => { const t = new Date(d + "T00:00:00"); return isNaN(t) ? d : t.toLocaleDateString(undefined, { day: "numeric", month: "short" }); };
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img">
    <line x1="${L}" x2="${W - R}" y1="${y(hi)}" y2="${y(hi)}" stroke="var(--line)"/>
    <line x1="${L}" x2="${W - R}" y1="${y(lo)}" y2="${y(lo)}" stroke="var(--line)"/>
    <text x="${L - 6}" y="${y(hi) + 4}" fill="var(--muted)" font-size="10" text-anchor="end">${h(fmt(hi))}</text>
    <text x="${L - 6}" y="${y(lo) + 4}" fill="var(--muted)" font-size="10" text-anchor="end">${h(fmt(lo))}</text>
    <text x="${L}" y="${H - 5}" fill="var(--muted)" font-size="10">${h(day(all[0][0]))}</text>
    <text x="${W - R}" y="${H - 5}" fill="var(--muted)" font-size="10" text-anchor="end">${h(day(all[all.length - 1][0]))}</text>
    <path d="${path}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
    <circle cx="${x(lastIndex)}" cy="${y(last[1])}" r="3.5" fill="${color}" stroke="var(--surface)" stroke-width="2"/>
    <text x="${x(lastIndex) + 7}" y="${y(last[1]) + 4}" fill="var(--text)" font-size="11" font-weight="700">${h(fmt(last[1]))}</text>
  </svg>`;
}
const money = (v) => info.symbol + v.toFixed(v >= 100 ? 0 : 2);
const pctFmt = (v) => Math.round(v * 100) + "%";

async function shareFile(name, type, data) {
  const file = new File([data], name, { type });
  if (navigator.canShare && navigator.canShare({ files: [file] })) {
    try { await navigator.share({ files: [file], title: name }); return; } catch (e) { if (e.name === "AbortError") return; }
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(file);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
}

function pickFile(accept) {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.onchange = () => resolve(input.files[0] || null);
    input.click();
  });
}

async function shrinkPhoto(file) {
  const img = await createImageBitmap(file);
  const scale = Math.min(1, 1200 / Math.max(img.width, img.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(img.width * scale);
  canvas.height = Math.round(img.height * scale);
  canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
  return new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.82));
}

const photoUrls = new Map();
async function photoUrl(id) {
  if (photoUrls.has(id)) return photoUrls.get(id);
  const blob = await idbGet("photos", id);
  const url = blob ? URL.createObjectURL(blob) : "";
  photoUrls.set(id, url);
  return url;
}
function forgetPhoto(id) {
  if (photoUrls.get(id)) URL.revokeObjectURL(photoUrls.get(id));
  photoUrls.delete(id);
}

/* --- navigation ---------------------------------------------------------------- */

const TITLES = { collection: "Collection", meta: "Meta tracker", market: "Market", insight: "Future insight", more: "More" };
function show(tab) {
  state.tab = tab;
  localStorage.setItem("tab", tab);
  $$(".tabbar button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  $("#title").textContent = TITLES[tab];
  $("#subtitle").textContent = "";
  ({ collection: renderCollection, meta: renderMeta, market: renderMarket, insight: renderInsight, more: renderMore })[tab]();
}
$$(".tabbar button").forEach((b) => { b.onclick = () => { window.scrollTo(0, 0); show(b.dataset.tab); }; });
const view = () => $("#view");
function rerender() { show(state.tab); }

/* --- collection ---------------------------------------------------------------- */

const COLLECTION_SORTS = [["", "Sort: game & set"], ["name", "Name"], ["value", "Value"], ["quantity", "Quantity"],
  ["number", "Card number"], ["date_added", "Date added"]];

function renderCollection() {
  const s = state.collection;
  const data = safe(() => api("collection", { view: s.view, search: s.search, game: s.game, sort: s.sort, reverse: s.reverse }));
  if (!data) return;
  $("#subtitle").textContent = data.status;
  const wish = s.view === "wishlist";
  view().innerHTML = `
    <div class="seg">
      <button data-v="collection" class="${!wish ? "on" : ""}">My collection</button>
      <button data-v="wishlist" class="${wish ? "on" : ""}">Wishlist</button>
    </div>
    <div class="tiles">
      <div class="tile"><div class="v gold">${h(data.tiles.value)}</div><div class="c">Collection value</div></div>
      <div class="tile"><div class="v">${h(data.tiles.cards)}</div><div class="c">Cards owned</div></div>
      <div class="tile"><div class="v">${h(data.tiles.unique)}</div><div class="c">Different cards</div></div>
      <div class="tile"><div class="v">${h(data.tiles.wishlist)}</div><div class="c">Wishlist to complete</div></div>
    </div>
    <div class="row"><input type="search" class="grow" id="c-search" placeholder="Search name, set, number…" value="${h(s.search)}">
      <button class="btn gold" id="c-add">+ Add</button></div>
    <div class="row">
      <select class="grow" id="c-game">${opts([["", "All games"], ...data.games], s.game)}</select>
      <select class="grow" id="c-sort">${opts(COLLECTION_SORTS, s.sort)}</select>
      <button class="btn small" id="c-rev">${s.reverse ? "↓" : "↑"}</button>
    </div>
    <div class="row">
      <button class="btn small" id="c-select">${s.select ? "Cancel" : "Select"}</button>
      ${s.select ? `<span class="muted">${s.selected.size} selected</span>` : `<button class="btn small" id="c-update-all">↻ Update prices</button>`}
    </div>
    ${s.select ? `<div class="row">
      <button class="btn small" id="c-sel-all">All</button>
      <button class="btn small" id="c-update" ${s.selected.size ? "" : "disabled"}>↻ Update prices</button>
      <button class="btn small" id="c-move" ${s.selected.size ? "" : "disabled"}>${wish ? "Mark as owned" : "Move to wishlist"}</button>
      <button class="btn small danger" id="c-delete" ${s.selected.size ? "" : "disabled"}>Delete</button></div>` : ""}
    <div class="list">${data.rows.map((r) => `
      <button class="item ${s.selected.has(r.id) ? "sel" : ""}" data-id="${r.id}">
        ${s.select ? `<span class="tick"></span>` : ""}
        ${r.photo ? `<img class="thumb" data-photo="${r.id}" alt="">` : ""}
        <span class="main"><div class="t">${h(r.name)}</div>
          <div class="s">${h([r.game, r.number || r.set, r.condition].filter(Boolean).join(" · "))}</div></span>
        <span class="end"><div class="t">${h(r.value)}</div><div class="s">×${r.quantity}</div></span>
      </button>`).join("") || `<div class="empty">${wish ? "Your wishlist is empty." : "No cards yet. Tap + Add, or add cards from the Market tab."}</div>`}
    </div>`;

  $$(".seg button").forEach((b) => { b.onclick = () => { s.view = b.dataset.v; s.selected.clear(); rerender(); }; });
  const search = $("#c-search");
  search.oninput = () => { s.search = search.value; clearTimeout(search.t); search.t = setTimeout(() => { renderCollection(); const el = $("#c-search"); el.focus(); el.setSelectionRange(el.value.length, el.value.length); }, 300); };
  $("#c-game").onchange = (e) => { s.game = e.target.value; rerender(); };
  $("#c-sort").onchange = (e) => { s.sort = e.target.value; s.reverse = ["value", "quantity", "date_added"].includes(s.sort); rerender(); };
  $("#c-rev").onclick = () => { s.reverse = !s.reverse; rerender(); };
  $("#c-add").onclick = () => cardForm(null);
  $("#c-select").onclick = () => { s.select = !s.select; s.selected.clear(); rerender(); };
  const ids = () => [...s.selected];
  if ($("#c-update-all")) $("#c-update-all").onclick = () => updatePrices(data.rows.map((r) => r.id));
  if (s.select) {
    $("#c-sel-all").onclick = () => { data.rows.forEach((r) => s.selected.add(r.id)); rerender(); };
    $("#c-update").onclick = () => updatePrices(ids());
    $("#c-move").onclick = () => { safe(() => api("move_cards", { ids: ids() })); s.selected.clear(); rerender(); };
    $("#c-delete").onclick = async () => {
      if (!confirm(`Delete ${s.selected.size} card${s.selected.size === 1 ? "" : "s"}?`)) return;
      for (const id of ids()) { await idbDel("photos", id); forgetPhoto(id); }
      safe(() => api("delete_cards", { ids: ids() }));
      s.selected.clear(); rerender();
    };
  }
  $$(".item[data-id]").forEach((el) => {
    el.onclick = () => {
      const id = Number(el.dataset.id);
      if (s.select) { s.selected.has(id) ? s.selected.delete(id) : s.selected.add(id); rerender(); }
      else cardDetails(id);
    };
  });
  $$("img[data-photo]").forEach(async (img) => { img.src = await photoUrl(Number(img.dataset.photo)); });
}

async function updatePrices(ids) {
  if (!ids.length) return;
  if (ids.length > 1 && !confirm(`Look up prices for ${ids.length} cards?`)) return;
  const r = await work(`Updating ${ids.length} price${ids.length === 1 ? "" : "s"}…`, () => api("update_prices", { ids }));
  if (r) toast(`Updated ${r.updated} of ${r.total}.` + (r.failed.length ? ` Couldn't price: ${r.failed.slice(0, 3).join("; ")}` : ""), r.failed.length > 0);
  rerender();
}

async function cardDetails(id) {
  const d = safe(() => api("card_details", { id }));
  if (!d) return;
  const url = d.photo ? await photoUrl(id) : "";
  const sh = sheet(d.name, `
    ${url ? `<img class="photo" src="${url}" alt="">` : ""}
    ${d.info.map((l) => `<div class="muted">${h(l)}</div>`).join("")}
    <div class="big">${h(d.value)}</div>
    ${d.paid ? `<div>${h(d.paid)}</div>` : ""}
    ${d.notes ? `<p class="note">${h(d.notes)}</p>` : ""}
    <div class="box"><h4>Price history <span class="muted">per copy</span></h4>${chart(d.history, money, false, "Price history builds up each time the price is updated.")}</div>
    <div class="btns">
      <button class="btn gold" data-a="edit">Edit</button>
      <button class="btn" data-a="price">↻ Update price</button>
      <button class="btn" data-a="move">${d.wishlist ? "Mark as owned" : "Move to wishlist"}</button>
      <button class="btn" data-a="sold">Log sold price</button>
      <button class="btn" data-a="ebay">eBay sold ↗</button>
      <button class="btn danger" data-a="delete">Delete</button>
    </div>`);
  const act = (a, fn) => { $(`[data-a=${a}]`, sh).onclick = fn; };
  act("edit", () => { sh.close(); cardForm(id); });
  act("price", async () => { sh.close(); await updatePrices([id]); cardDetails(id); });
  act("move", () => { safe(() => api("move_cards", { ids: [id] })); sh.close(); rerender(); });
  act("sold", () => soldSheet(d.name, ""));
  act("ebay", () => { const f = api("card", { id }); window.open(ebayUrl(f.name, f.game), "_blank"); });
  act("delete", async () => {
    if (!confirm(`Delete ${d.name}?`)) return;
    await idbDel("photos", id); forgetPhoto(id);
    safe(() => api("delete_cards", { ids: [id] }));
    sh.close(); rerender();
  });
}

function ebayUrl(name, game) {
  const q = (game && !/riftbound/i.test(game)) ? name : `${name} riftbound`;
  return `https://www.${info.ebay_site}/sch/i.html?_nkw=${encodeURIComponent(q).replace(/%20/g, "+")}&LH_Sold=1&LH_Complete=1&_sop=13`;
}

async function cardForm(id, prefill) {
  const f = prefill || safe(() => api("card", { id }));
  if (!f) return;
  let photo = f.photo && f.id ? await idbGet("photos", f.id) : null;
  let photoChanged = false;
  const games = [...new Set([...f.games, f.game].filter(Boolean))];
  const sh = sheet(f.id ? "Edit card" : "Add card", `
    <div class="form">
      <label class="f">Name *</label><input id="f-name" value="${h(f.name)}" autocomplete="off">
      <div class="two">
        <div><label class="f">Game</label><select id="f-game">${opts(games, f.game)}</select></div>
        <div><label class="f">Card number</label><input id="f-number" value="${h(f.number)}" placeholder="e.g. OGN-202a"></div>
      </div>
      <label class="f">Set</label><input id="f-set" value="${h(f.set_name)}">
      <div class="two">
        <div><label class="f">Rarity</label><input id="f-rarity" list="f-rarities" value="${h(f.rarity)}">
          <datalist id="f-rarities">${opts(f.rarities)}</datalist></div>
        <div><label class="f">Condition</label><select id="f-condition">${opts(["", ...f.conditions], f.condition)}</select></div>
      </div>
      <div class="two">
        <div><label class="f">Quantity</label><input id="f-qty" type="number" inputmode="numeric" min="0" value="${h(f.quantity)}"></div>
        <div><label class="f">Paid each (${h(f.symbol)})</label><input id="f-paid" inputmode="decimal" value="${h(f.paid)}"></div>
      </div>
      <label class="f">Value each (${h(f.symbol)})</label>
      <div class="row"><input id="f-value" class="grow" inputmode="decimal" value="${h(f.value)}">
        <button class="btn" id="f-lookup">Look up price</button></div>
      <div class="note" id="f-lookup-msg"></div>
      <label class="f">Photo</label>
      <div class="row"><button class="btn small" id="f-photo">${photo ? "Change photo" : "Take or choose photo"}</button>
        ${photo ? `<button class="btn small danger" id="f-photo-rm">Remove</button>` : ""}</div>
      <img class="photo" id="f-photo-img" alt="" style="${photo ? "" : "display:none"}">
      <label class="check" style="margin-top:12px"><input type="checkbox" id="f-wish" ${f.wishlist ? "checked" : ""}> On my wishlist (I don't own it yet)</label>
      <label class="f">Notes</label><textarea id="f-notes">${h(f.notes)}</textarea>
      <div class="btns"><button class="btn gold wide" id="f-save">Save card</button></div>
    </div>`);
  const get = (sel) => $(sel, sh).value;
  const fields = () => ({ id: f.id, name: get("#f-name"), game: get("#f-game"), number: get("#f-number"), set_name: get("#f-set"),
    rarity: get("#f-rarity"), condition: get("#f-condition"), quantity: get("#f-qty"), paid: get("#f-paid"),
    value: get("#f-value"), value_original: f.value, notes: get("#f-notes"), wishlist: $("#f-wish", sh).checked,
    photo: !!photo });
  const img = $("#f-photo-img", sh);
  if (photo) img.src = URL.createObjectURL(photo);
  $("#f-photo", sh).onclick = async () => {
    const file = await pickFile("image/*");
    if (!file) return;
    photo = await shrinkPhoto(file);
    photoChanged = true;
    img.src = URL.createObjectURL(photo);
    img.style.display = "";
  };
  if ($("#f-photo-rm", sh)) $("#f-photo-rm", sh).onclick = () => { photo = null; photoChanged = true; img.style.display = "none"; };
  $("#f-lookup", sh).onclick = async () => {
    const r = await work("Looking up price…", () => api("lookup_price", { fields: fields() }));
    if (r) { $("#f-value", sh).value = r.value; $("#f-lookup-msg", sh).textContent = r.text; }
  };
  $("#f-save", sh).onclick = async () => {
    if (!get("#f-name").trim()) { toast("Please enter the card's name.", true); return; }
    const newId = safe(() => api("save_card", { fields: fields() }));
    if (newId === undefined) return;
    if (photoChanged || (prefill && photo)) {
      forgetPhoto(newId);
      if (photo) await idbPut("photos", newId, photo); else await idbDel("photos", newId);
    }
    sh.close();
    toast("Saved.");
    rerender();
  };
}

/* --- sold prices ------------------------------------------------------------------ */

function soldSheet(name, code, onChange) {
  let data = safe(() => api("sold", { name, code }));
  if (!data) return;
  const sh = sheet("Sold prices", `
    <div class="gold" style="font-weight:700">${h(name)}${code ? ` · ${h(code)}` : ""}</div>
    <p class="note">Add each sold price you see, one at a time: what one copy sold for, in ${h(data.label)}.
      Tap eBay sold ↗ to see the listings.</p>
    <div class="form">
      <div class="two">
        <div><label class="f">Sold for (${h(data.symbol)})</label><input id="s-price" inputmode="decimal" placeholder="e.g. 4.50"></div>
        <div><label class="f">Date sold</label><input id="s-day" type="date" value="${h(data.today)}"></div>
      </div>
      <label class="f">Note</label><input id="s-note" placeholder="optional, e.g. near mint, foil">
      <div class="btns"><button class="btn gold" id="s-add">Add price</button><button class="btn" id="s-ebay">eBay sold ↗</button></div>
    </div>
    <h2>Logged so far</h2><div class="list" id="s-list"></div>`, onChange);
  const draw = () => {
    $("#s-list", sh).innerHTML = data.rows.map((r) => `<div class="item"><span class="main"><div class="t">${h(r.price)}${r.code ? ` <span class="pill">${h(r.code)}</span>` : ""}</div>
      <div class="s">${h(r.day)}${r.note ? " · " + h(r.note) : ""}</div></span>
      <button class="btn small danger" data-del="${r.id}">Remove</button></div>`).join("") || `<div class="empty">Nothing logged yet.</div>`;
    $$("[data-del]", sh).forEach((b) => { b.onclick = () => { const d = safe(() => api("sold_delete", { id: Number(b.dataset.del), name, code })); if (d) { data = d; draw(); } }; });
  };
  draw();
  $("#s-add", sh).onclick = () => {
    const d = safe(() => api("sold_add", { name, code, price: $("#s-price", sh).value, day: $("#s-day", sh).value, note: $("#s-note", sh).value }));
    if (d) { data = d; $("#s-price", sh).value = ""; $("#s-note", sh).value = ""; draw(); }
  };
  $("#s-ebay", sh).onclick = () => window.open(ebayUrl(name, "Riftbound"), "_blank");
}

/* --- meta tracker ---------------------------------------------------------------- */

function renderMeta() {
  const s = state.meta;
  const data = safe(() => api("meta", { legend: s.legend, since: s.since, top: s.top, runes: s.runes }));
  if (!data) return;
  $("#subtitle").textContent = data.status;
  const sec = s.section;
  view().innerHTML = `
    <div class="tiles">
      <div class="tile"><div class="v">${h(data.tiles.decks)}</div><div class="c">Decklists</div></div>
      <div class="tile"><div class="v">${h(data.tiles.events)}</div><div class="c">Events</div></div>
      <div class="tile"><div class="v gold">${h(data.tiles.legend)}</div><div class="c">Most played legend</div></div>
      <div class="tile"><div class="v">${h(data.tiles.short)}</div><div class="c">Meta cards you're short on</div></div>
    </div>
    <div class="row"><select class="grow" id="m-legend">${opts(data.legends, s.legend)}</select></div>
    <div class="row">
      <select class="grow" id="m-top">${opts(data.tops, s.top)}</select>
      <input class="grow" type="date" id="m-since" value="${h(s.since)}" aria-label="Since">
      <label class="check"><input type="checkbox" id="m-runes" ${s.runes ? "checked" : ""}> Runes</label>
    </div>
    <div class="seg">
      <button data-s="cards" class="${sec === "cards" ? "on" : ""}">Most played</button>
      <button data-s="legends" class="${sec === "legends" ? "on" : ""}">Legends</button>
      <button data-s="decks" class="${sec === "decks" ? "on" : ""}">Decklists</button>
    </div>
    <div id="m-body"></div>
    ${data.topdeck ? `<p class="status"><a href="https://topdeck.gg" target="_blank" rel="noopener">Tournament data provided by TopDeck.gg ↗</a></p>` : ""}`;
  $("#m-legend").onchange = (e) => { s.legend = e.target.value; rerender(); };
  $("#m-top").onchange = (e) => { s.top = e.target.value; rerender(); };
  $("#m-since").onchange = (e) => { s.since = e.target.value; rerender(); };
  $("#m-runes").onchange = (e) => { s.runes = e.target.checked; rerender(); };
  $$(".seg button").forEach((b) => { b.onclick = () => { s.section = b.dataset.s; s.select = false; s.selected.clear(); rerender(); }; });
  const body = $("#m-body");

  if (sec === "cards") {
    body.innerHTML = `
      <div class="row"><button class="btn small" id="m-select">${s.select ? "Cancel" : "Select"}</button>
        ${s.select ? `<button class="btn small" id="m-all">All</button>
          <button class="btn small gold" id="m-wish" ${s.selected.size ? "" : "disabled"}>★ Add missing to wishlist (${s.selected.size})</button>` : ""}</div>
      <p class="note">Play rate: share of decks running the card. <span class="have">Green</span>: you own enough copies.
        <span class="short">Red</span>: fewer than the average played.</p>
      <div class="list">${data.usage.map((u) => `
        <button class="item ${s.selected.has(u.name) ? "sel" : ""}" data-name="${h(u.name)}">
          ${s.select ? `<span class="tick"></span>` : ""}
          <span class="main"><div class="t">${h(u.name)}</div><div class="s">${h(u.section)} · ${h(u.avg)} copies · ${u.decks} decks</div></span>
          <span class="end"><div class="t">${h(u.share)}</div><div class="s ${u.short ? "short" : "have"}">own ${u.owned}</div></span>
        </button>`).join("") || `<div class="empty">No decklists match.</div>`}</div>`;
    $("#m-select").onclick = () => { s.select = !s.select; s.selected.clear(); rerender(); };
    if (s.select) {
      $("#m-all").onclick = () => { data.usage.forEach((u) => s.selected.add(u.name)); rerender(); };
      $("#m-wish").onclick = () => {
        const msg = safe(() => api("add_missing", { names: [...s.selected], legend: s.legend, since: s.since, top: s.top, runes: s.runes }));
        if (msg) toast(msg);
        s.select = false; s.selected.clear(); rerender();
      };
    }
    $$(".item[data-name]", body).forEach((el) => {
      el.onclick = () => {
        const n = el.dataset.name;
        if (s.select) { s.selected.has(n) ? s.selected.delete(n) : s.selected.add(n); rerender(); }
        else { state.market.view = "catalog"; state.market.search = n; state.market.limit = 150; show("market"); }
      };
    });
  } else if (sec === "legends") {
    body.innerHTML = `<p class="note">Tap a legend to see the cards its decks play.</p>
      <div class="list">${data.shares.map((l) => `
        <button class="item" data-legend="${h(l.legend)}"><span class="main"><div class="t">${h(l.legend)}</div>
          <div class="s">${l.decks} decks${l.best ? ` · best finish ${h(l.best)}` : ""}</div></span>
          <span class="end"><div class="t">${h(l.share)}</div></span></button>`).join("") || `<div class="empty">No decklists yet.</div>`}</div>`;
    $$("[data-legend]", body).forEach((el) => { el.onclick = () => { s.legend = el.dataset.legend; s.section = "cards"; rerender(); }; });
  } else {
    body.innerHTML = `<div class="row"><button class="btn small gold" id="m-add">+ Add decklist</button></div>
      <div class="list">${data.decks.map((d) => `
        <button class="item" data-deck="${d.id}"><span class="main"><div class="t">${h(d.legend || "(no legend)")}${d.mine ? ` <span class="pill">yours</span>` : ""}</div>
          <div class="s">${h([d.date, d.event, d.player].filter(Boolean).join(" · "))}</div></span>
          <span class="end"><div class="t">${d.placement ? "#" + h(d.placement) : ""}</div></span></button>`).join("") || `<div class="empty">No decklists match.</div>`}</div>
      ${data.more_decks ? `<p class="status">…and ${data.more_decks} more. Use the filters to narrow the list.</p>` : ""}
      <p class="note">Tournament and community decklists download automatically every day.</p>`;
    $("#m-add").onclick = deckForm;
    $$("[data-deck]", body).forEach((el) => { el.onclick = () => deckSheet(Number(el.dataset.deck)); });
  }
}

function deckSheet(id) {
  const d = safe(() => api("deck", { id }));
  if (!d) return;
  const sh = sheet(d.title, `<div class="muted">${h(d.header)}</div>
    <p class="note"><span class="short">Red</span> = you own fewer copies than this list plays.</p>
    ${d.sections.map((sec) => `<h2>${h(sec.name)} <span class="muted">(${sec.count})</span></h2>
      <div class="list">${sec.cards.map((c) => `<div class="item"><span class="main"><div class="t ${c.short ? "short" : ""}">${c.qty} × ${h(c.name)}</div></span>
        <span class="end"><div class="s">own ${c.owned}</div></span></div>`).join("")}</div>`).join("")}
    <div class="btns"><button class="btn danger wide" id="d-del">Delete decklist</button></div>
    ${d.mine ? "" : `<p class="note">Imported decklists come back with the next daily download.</p>`}`);
  $("#d-del", sh).onclick = () => {
    if (!confirm("Delete this decklist?")) return;
    safe(() => api("delete_decks", { ids: [id] }));
    sh.close(); rerender();
  };
}

function deckForm() {
  const sh = sheet("Add decklist", `<div class="form">
    <label class="f">Decklist *</label>
    <textarea id="k-text" style="min-height:180px" placeholder="Paste a decklist, one card per line, e.g.&#10;Legend:&#10;1 Jinx, Loose Cannon&#10;Main Deck:&#10;3 Get Excited!"></textarea>
    <div class="two"><div><label class="f">Player</label><input id="k-player"></div>
      <div><label class="f">Placement</label><input id="k-place" inputmode="numeric" placeholder="e.g. 1"></div></div>
    <label class="f">Event</label><input id="k-event">
    <div class="two"><div><label class="f">Date</label><input id="k-date" type="date"></div>
      <div><label class="f">Deck name</label><input id="k-name"></div></div>
    <div class="btns"><button class="btn gold wide" id="k-save">Save decklist</button></div></div>`);
  $("#k-save", sh).onclick = () => {
    const v = (s) => $(s, sh).value;
    const ok = safe(() => api("add_deck", { text: v("#k-text"), name: v("#k-name"), player: v("#k-player"), event: v("#k-event"), placement: v("#k-place"), day: v("#k-date") }));
    if (ok !== undefined) { sh.close(); toast("Decklist saved."); rerender(); }
  };
}

/* --- market -------------------------------------------------------------------- */

const CATALOG_SORTS = [["price", "Price"], ["ebay", "eBay sold"], ["d7", "7-day change"], ["d1", "1-day change"],
  ["cm", "EU price"], ["play", "Play rate"], ["meta", "Meta move"], ["owned", "Own"], ["number", "Card number"],
  ["name", "Name"], ["version", "Version"]];
const SIGNAL_SORTS = [["", "Strongest signal"], ["name", "Name"], ["owned", "Own"], ["value", "Value"], ["ebay", "eBay sold"],
  ["profit", "Profit"], ["price", "Price move"], ["play", "Play rate"], ["meta", "Meta move"]];

function renderMarket() {
  const s = state.market;
  const views = [["catalog", "All cards"], ["mine", "My cards"], ["opportunities", "Buy opportunities"], ["all", "Everything"]];
  view().innerHTML = `
    <div class="seg">${views.map(([v, l]) => `<button data-v="${v}" class="${s.view === v ? "on" : ""}">${l}</button>`).join("")}</div>
    <div class="row"><span class="muted">Compare</span>
      <select class="grow" id="k-days">${opts([[7, "Last 7 days"], [14, "Last 14 days"], [30, "Last 30 days"]], s.days)}</select></div>
    <div id="k-body"></div>`;
  $$(".seg button").forEach((b) => { b.onclick = () => { s.view = b.dataset.v; rerender(); }; });
  $("#k-days").onchange = (e) => { s.days = Number(e.target.value); rerender(); };
  if (s.view === "catalog") renderCatalog(); else renderSignals();
}

function renderCatalog() {
  const s = state.market;
  const data = safe(() => api("catalog_list", { search: s.search, set_name: s.set, rarity: s.rarity, version: s.version,
    meta_filter: s.metaFilter, min1: s.min1, days: s.days, sort: s.sort, reverse: s.reverse, limit: s.limit }));
  if (!data) return;
  $("#subtitle").textContent = `${data.shown.toLocaleString()} of ${data.total.toLocaleString()} printings · prices in ${info.currency}` + (data.updated ? ` · updated ${data.updated.toLowerCase()}` : "");
  const body = $("#k-body");
  if (!data.total) {
    body.innerHTML = `<div class="empty">No card prices yet. They download automatically each day; check your connection and reopen the app.</div>`;
    return;
  }
  const active = [s.set, s.rarity, s.version, s.metaFilter !== "Any meta" ? s.metaFilter : ""].filter(Boolean).length;
  body.innerHTML = `
    <div class="row"><input type="search" class="grow" id="a-search" placeholder="Search name or number (e.g. OGN-202)" value="${h(s.search)}">
      <button class="btn small ${active ? "gold" : ""}" id="a-filters">Filters${active ? ` (${active})` : ""}</button></div>
    <div id="a-filter-rows" style="${s.filters ? "" : "display:none"}">
    <div class="row">
      <select class="grow" id="a-set">${opts([["", "All sets"], ...data.sets], s.set)}</select>
      <select class="grow" id="a-rarity">${opts([["", "All rarities"], ...data.rarities], s.rarity)}</select>
    </div>
    <div class="row">
      <select class="grow" id="a-version">${opts([["", "All versions"], ...data.versions], s.version)}</select>
      <select class="grow" id="a-meta">${opts(data.meta_filters, s.metaFilter)}</select>
    </div></div>
    <div class="row">
      <select class="grow" id="a-sort">${opts(CATALOG_SORTS.map(([v, l]) => [v, "Sort: " + l]), s.sort)}</select>
      <button class="btn small" id="a-rev">${s.reverse ? "↓" : "↑"}</button>
      <label class="check"><input type="checkbox" id="a-min" ${s.min1 ? "checked" : ""}> ${h(info.symbol)}1+</label>
    </div>
    ${data.note ? `<p class="note">${h(data.note)}</p>` : ""}
    <div class="list">${data.rows.map((r) => `
      <button class="item" data-code="${h(r.code)}">
        <span class="main"><div class="t">${h(r.name)}${r.version !== "Standard" ? ` <span class="pill">${h(r.version)}</span>` : ""}</div>
          <div class="s">${h(r.code)}${r.d7 ? ` · <span class="${r.trend}">${h(r.d7)} wk</span>` : ""}${r.meta ? ` · meta ${h(r.meta)}` : ""}${r.play ? ` · played ${h(r.play)}` : ""}${r.owned ? ` · own ${h(r.owned)}` : ""}</div></span>
        <span class="end"><div class="t ${r.trend}">${h(r.price)}</div><div class="s">${r.ebay ? "eBay " + h(r.ebay) : h(r.cm)}</div></span>
      </button>`).join("") || `<div class="empty">No cards match.</div>`}</div>
    ${data.shown > data.rows.length ? `<button class="btn more" id="a-more">Show more (${data.shown - data.rows.length} left)</button>` : ""}
    <p class="status">TCGplayer market prices via riftbound.gg, converted to ${h(info.currency)}. Green/red = up/down 5%+ this week.</p>`;
  const search = $("#a-search");
  search.oninput = () => { s.search = search.value; s.limit = 150; clearTimeout(search.t); search.t = setTimeout(() => { renderCatalog(); const el = $("#a-search"); el.focus(); el.setSelectionRange(el.value.length, el.value.length); }, 350); };
  const pick = (id, key) => { $(id).onchange = (e) => { s[key] = e.target.value; s.limit = 150; renderCatalog(); }; };
  $("#a-filters").onclick = () => { s.filters = !s.filters; $("#a-filter-rows").style.display = s.filters ? "" : "none"; };
  pick("#a-set", "set"); pick("#a-rarity", "rarity"); pick("#a-version", "version"); pick("#a-meta", "metaFilter");
  $("#a-sort").onchange = (e) => { s.sort = e.target.value; s.reverse = !["name", "number", "version"].includes(s.sort); renderCatalog(); };
  $("#a-rev").onclick = () => { s.reverse = !s.reverse; renderCatalog(); };
  $("#a-min").onchange = (e) => { s.min1 = e.target.checked; renderCatalog(); };
  if ($("#a-more")) $("#a-more").onclick = () => { s.limit += 300; renderCatalog(); };
  $$("[data-code]", body).forEach((el) => { el.onclick = () => catalogSheet(el.dataset.code); });
}

function catalogSheet(code) {
  const d = safe(() => api("catalog_card", { code, days: state.market.days }));
  if (!d) return;
  const sh = sheet(d.name, `
    <div class="muted">${h(d.info)}</div>${d.detail ? `<div class="muted">${h(d.detail)}</div>` : ""}
    <div class="big">${h(d.price)}</div>
    <ul class="facts">${d.facts.map((f) => `<li>${h(f)}</li>`).join("")}</ul>
    <div class="btns">
      <button class="btn gold" data-a="add">+ Add to collection</button>
      <button class="btn" data-a="wish">★ Add to wishlist</button>
      <button class="btn" data-a="ebay">eBay sold ↗</button>
      <button class="btn" data-a="sold">Log sold price</button>
    </div>
    <div class="box"><h4>Price history <span class="muted">this printing, per copy</span></h4>
      ${chart(d.history, money, false, "Price history builds up day by day. The 7-day change above works already.")}</div>
    <div class="box"><h4>Play rate <span class="muted">% of decklists, weekly</span></h4>
      ${chart(d.play, pctFmt, true, "Not being played in the decklists.")}</div>
    ${d.image ? `<img class="cardimg" src="${h(d.image)}" alt="${h(d.name)}" loading="lazy">` : ""}`);
  const act = (a, fn) => { $(`[data-a=${a}]`, sh).onclick = fn; };
  act("add", () => { const f = safe(() => api("catalog_form", { code })); if (f) { sh.close(); cardForm(null, f); } });
  act("wish", () => { const m = safe(() => api("catalog_wishlist", { code })); if (m) toast(m); });
  act("ebay", () => window.open(d.ebay_url, "_blank"));
  act("sold", () => soldSheet(d.name, d.code, () => { sh.close(); renderCatalog(); catalogSheet(code); }));
}

function renderSignals() {
  const s = state.market;
  const data = safe(() => api("signals", { view: s.view, days: s.days, signal: s.signal, sort: s.ssort, reverse: s.sreverse }));
  if (!data) return;
  $("#subtitle").textContent = `${data.rows.length} card${data.rows.length === 1 ? "" : "s"}`;
  const body = $("#k-body");
  body.innerHTML = `
    <div class="tiles">
      <div class="tile"><div class="v gold">${h(data.tiles.profit)}</div><div class="c">Profit on cards with a price paid</div></div>
      <div class="tile"><div class="v">${h(data.tiles.sell)}</div><div class="c">Sell signals</div></div>
      <div class="tile"><div class="v">${h(data.tiles.buy)}</div><div class="c">Buy early</div></div>
      <div class="tile"><div class="v">${h(data.tiles.updated)}</div><div class="c">Prices updated</div></div>
    </div>
    <div class="row">
      <select class="grow" id="g-signal">${opts(data.signals, s.signal)}</select>
      <select class="grow" id="g-sort">${opts(SIGNAL_SORTS, s.ssort)}</select>
      <button class="btn small" id="g-rev">${s.sreverse ? "↓" : "↑"}</button>
    </div>
    <div class="list">${data.rows.map((r) => `
      <button class="item" data-name="${h(r.name)}">
        <span class="main"><div class="t">${h(r.name)}</div>
          <div class="s"><span class="tag-${r.tag}">${h(r.signal)}</span>${r.meta ? ` · meta ${h(r.meta)}` : ""}${r.price ? ` · price ${h(r.price)}` : ""}</div></span>
        <span class="end"><div class="t">${h(r.value)}</div><div class="s">${r.profit ? h(r.profit) : r.owned ? "own " + h(r.owned) : h(r.play)}</div></span>
      </button>`).join("") || `<div class="empty">${s.view === "mine" ? "No cards in your collection yet. Add some from All cards." : "Nothing here yet. It fills in as decklists and prices build up."}</div>`}</div>
    <p class="status">${h(data.status)}</p>`;
  $("#g-signal").onchange = (e) => { s.signal = e.target.value; renderSignals(); };
  $("#g-sort").onchange = (e) => { s.ssort = e.target.value; s.sreverse = s.ssort !== "name"; renderSignals(); };
  $("#g-rev").onclick = () => { s.sreverse = !s.sreverse; renderSignals(); };
  $$("[data-name]", body).forEach((el) => { el.onclick = () => signalSheet(el.dataset.name); });
}

function signalSheet(name) {
  const d = safe(() => api("signal_card", { name, days: state.market.days }));
  if (!d) return;
  const sh = sheet(d.name, `
    <div class="big tag-${d.tag}">${h(d.signal)}</div>
    <p>${h(d.reason)}</p>
    ${d.facts ? `<p class="muted">${h(d.facts)}</p>` : ""}
    <p>${h(d.ebay)}</p>
    <div class="btns"><button class="btn" data-a="ebay">eBay sold ↗</button><button class="btn" data-a="sold">Log sold price</button>
      <button class="btn wide" data-a="all">See every printing in All cards</button></div>
    <div class="box"><h4>Price <span class="muted">per copy</span></h4>${chart(d.history, money, false, "Price history builds up day by day.")}</div>
    <div class="box"><h4>Play rate <span class="muted">% of decklists, weekly</span></h4>${chart(d.play, pctFmt, true, "Not being played in the decklists.")}</div>`);
  $("[data-a=ebay]", sh).onclick = () => window.open(d.ebay_url, "_blank");
  $("[data-a=sold]", sh).onclick = () => soldSheet(d.name, "", () => { sh.close(); renderSignals(); signalSheet(name); });
  $("[data-a=all]", sh).onclick = () => { sh.close(); const s = state.market; s.view = "catalog"; s.search = d.name; s.limit = 150; rerender(); };
}

/* --- insight ---------------------------------------------------------------- */

function renderInsight() {
  const s = state.insight;
  const data = safe(() => api("insight_report", { weeks: s.weeks, view: s.view }));
  if (!data) return;
  view().innerHTML = `
    <div class="tiles">
      <div class="tile"><div class="v gold">${h(data.tiles.watch)}</div><div class="c">Cards to watch (30+)</div></div>
      <div class="tile"><div class="v">${h(data.tiles.legends)}</div><div class="c">Rising legends</div></div>
      <div class="tile"><div class="v">${h(data.tiles.decks)}</div><div class="c">Decklists analysed</div></div>
      <div class="tile"><div class="v">${h(data.tiles.data)}</div><div class="c">With placings / records</div></div>
    </div>
    <div class="seg">${[["all", "All cards"], ["owned", "I own"], ["not_owned", "I don't own"]].map(([v, l]) => `<button data-v="${v}" class="${s.view === v ? "on" : ""}">${l}</button>`).join("")}</div>
    <div class="row"><span class="muted">Look at</span><select class="grow" id="i-weeks">${opts(data.weeks, s.weeks)}</select></div>
    <div class="list">${data.rows.map((r, i) => `
      <button class="item" data-i="${i}"><span class="main"><div class="t">${h(r.name)}</div><div class="s">${h(r.tags)}</div></span>
        <span class="end"><div class="t tier-${r.tier}">${r.score} <span style="letter-spacing:-1px">${h(r.bar)}</span></div>
          <div class="s">${h(r.play)}${r.owned ? " · own " + h(r.owned) : ""}</div></span></button>`).join("") ||
      `<div class="empty">No card shows early signs right now.</div>`}</div>
    <p class="status">${h(data.status)}</p>
    ${data.legends.length ? `<h2>Legends on the move <span class="muted">meta share, second half vs first half</span></h2>
      <div class="list">${data.legends.map((l) => `<div class="item"><span class="main"><div class="t">${h(l.legend)}</div><div class="s">${h(l.share)}</div></span>
        <span class="end"><div class="t ${l.trend === "Rising" ? "up" : l.trend === "Falling" ? "down" : ""}">${h(l.trend)}</div><div class="s">${h(l.change)}</div></span></div>`).join("")}</div>` : ""}`;
  $$(".seg button").forEach((b) => { b.onclick = () => { s.view = b.dataset.v; rerender(); }; });
  $("#i-weeks").onchange = (e) => { s.weeks = e.target.value; rerender(); };
  $$("[data-i]").forEach((el) => {
    el.onclick = () => {
      const r = data.rows[Number(el.dataset.i)];
      const sh = sheet(r.name, `<div class="big">Watch score ${r.score} / 100</div>
        <div class="muted">Played ${h(r.play)}${r.top ? ` · top finishers ${h(r.top)}` : ""}${r.win ? ` · win rate ${h(r.win)}` : ""}${r.owned ? ` · you own ${h(r.owned)}` : ""}</div>
        ${r.signs.map((g) => `<div class="box"><h4>▸ ${h(g.kind)}</h4><div>${h(g.detail)}</div></div>`).join("")}
        <div class="btns"><button class="btn gold" data-a="wish">★ Add to wishlist</button><button class="btn" data-a="ebay">eBay sold ↗</button>
          <button class="btn wide" data-a="all">See its prices in All cards</button></div>`);
      $("[data-a=wish]", sh).onclick = () => { const m = safe(() => api("wishlist_names", { names: [r.name], note: `Added from Future insight (watch score ${r.score}: ${r.tags})` })); if (m) toast(m); };
      $("[data-a=ebay]", sh).onclick = () => window.open(r.ebay_url, "_blank");
      $("[data-a=all]", sh).onclick = () => { sh.close(); const m = state.market; m.view = "catalog"; m.search = r.name; m.limit = 150; show("market"); };
    };
  });
}

/* --- more ------------------------------------------------------------------ */

function renderMore() {
  info = safe(() => api("summary")) || info;
  const built = dataInfo && dataInfo.built ? new Date(dataInfo.built) : null;
  view().innerHTML = `
    <h2>Settings</h2>
    <div class="box">
      <label class="f note">Currency</label>
      <select id="o-currency">${opts(info.choices, info.currency_label)}</select>
      <p class="note">${h(info.rate)}. Prices you type (paid, sold) are in this currency.</p>
      <label class="f note">eBay site for sold listings</label>
      <select id="o-ebay">${opts(info.ebay_sites, info.ebay_site)}</select>
    </div>
    <h2>Data</h2>
    <div class="box">
      <div>${info.printings.toLocaleString()} printings with prices · ${info.decks.toLocaleString()} decklists</div>
      <div class="note">Prices, exchange rates and decklists (riftbound.gg${info.topdeck ? " and TopDeck.gg" : ""}) update automatically once a day${built ? `; last update ${built.toLocaleString()}` : ""}.</div>
      ${dataInfo && dataInfo.problems && dataInfo.problems.length ? `<div class="note err">Last update had problems: ${h(dataInfo.problems.join("; "))}</div>` : ""}
      ${info.topdeck ? `<p class="note"><a href="https://topdeck.gg" target="_blank" rel="noopener">Tournament data provided by TopDeck.gg ↗</a></p>` : ""}
      <div class="btns"><button class="btn wide" id="o-reload">↻ Check for new data</button></div>
    </div>
    <h2>Your collection</h2>
    <div class="box">
      <p class="note">Your collection, wishlist, sold prices, photos and settings are saved on this phone only.
        Use a backup to move them to a new phone, and CSV to move cards to or from the PC app.</p>
      <div class="btns">
        <button class="btn" id="o-export">Export CSV</button>
        <button class="btn" id="o-import">Import CSV</button>
        <button class="btn" id="o-backup">Back up</button>
        <button class="btn" id="o-restore">Restore backup</button>
      </div>
    </div>
    <h2>Install on your iPhone</h2>
    <div class="box note">In Safari, tap the Share button, then <b>Add to Home Screen</b>. The app then opens full screen from its own icon,
      and keeps working offline with the last data it downloaded.</div>
    <p class="status">Card Collection Logger ${h(dataInfo ? dataInfo.version : "")} · prices from TCGplayer via riftbound.gg and Cardmarket ·
      exchange rates from the European Central Bank · eBay sold prices are the ones you log.</p>`;
  $("#o-currency").onchange = (e) => { info = safe(() => api("set_currency", { code: e.target.value })) || info; rerender(); };
  $("#o-ebay").onchange = (e) => { info = safe(() => api("set_ebay_site", { site: e.target.value })) || info; rerender(); };
  $("#o-reload").onclick = () => { localStorage.removeItem("market_built"); location.reload(); };
  $("#o-export").onclick = () => { const csv = safe(() => api("export_csv")); if (csv) shareFile("card_collection.csv", "text/csv", csv); };
  $("#o-import").onclick = async () => {
    const file = await pickFile(".csv,text/csv");
    if (!file) return;
    const text = await file.text();
    const n = await work("Importing…", () => api("import_csv", { text }));
    if (n !== undefined) toast(`Imported ${n} card${n === 1 ? "" : "s"}.`);
  };
  $("#o-backup").onclick = async () => {
    await persist();
    const photos = {};
    const db = await idb();
    await new Promise((resolve) => {
      const req = db.transaction("photos").objectStore("photos").openCursor();
      req.onsuccess = async () => { const c = req.result; if (c) { photos[c.key] = c.value; c.continue(); } else resolve(); };
    });
    const parts = [py.FS.readFile(USER_DB)];
    const index = [];
    for (const [key, blob] of Object.entries(photos)) { index.push([key, blob.size]); parts.push(new Uint8Array(await blob.arrayBuffer())); }
    const header = new TextEncoder().encode(JSON.stringify({ app: "card-logger", db: parts[0].length, photos: index }) + "\n");
    shareFile(`card_logger_backup_${new Date().toISOString().slice(0, 10)}.cardlogger`, "application/octet-stream", new Blob([header, ...parts]));
  };
  $("#o-restore").onclick = async () => {
    const file = await pickFile(".cardlogger,application/octet-stream");
    if (!file || !confirm("Replace everything on this phone with the backup?")) return;
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const nl = bytes.indexOf(10);
      const head = JSON.parse(new TextDecoder().decode(bytes.slice(0, nl)));
      if (head.app !== "card-logger") throw new Error("That isn't a Card Logger backup.");
      let at = nl + 1;
      await idbPut("files", "user.db", bytes.slice(at, at + head.db));
      at += head.db;
      for (const [key, size] of head.photos) { await idbPut("photos", Number(key), new Blob([bytes.slice(at, at + size)], { type: "image/jpeg" })); at += size; }
      localStorage.removeItem("market_built");
      location.reload();
    } catch (e) { toast(e.message, true); }
  };
}

boot();
