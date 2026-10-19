"use strict";

// ---------------------------------------------------------------------------
// Languages. All texts live in www/lang/<code>/ (strings.json, help.html,
// add-camera.html); camwall.py lists the available ones in lang/languages.json.
// English is the fallback for any text a translation is missing.
// ---------------------------------------------------------------------------

const BASE_LANG = "en";
// Shown only if even the language files cannot be loaded (e.g. page opened as a file).
const BUILTIN = {
  loadFailFile: "This page was opened as a file. Start CamWall (start.bat / start.sh) and open http://localhost:1984 instead.",
  loadFail: "Could not load the camera list. Make sure CamWall is running (start.bat / start.sh) and reload this page.",
};

function storageGet(key) { try { return localStorage.getItem(key); } catch { return null; } }
function storageSet(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } }

let languages = [];  // [{ code, label, name, locale }]
let lang = BASE_LANG;
let locale = "en-GB";
let texts = {};      // current language merged over English
const packs = {};    // code -> { strings, help, addHelp }

async function fetchText(url) {
  const res = await fetch(url, { cache: "no-store" });  // edits show up on a plain reload
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.text();
}

async function loadPack(code) {
  if (packs[code]) return packs[code];
  const optional = (file) => fetchText(`lang/${code}/${file}`).catch(() => "");  // "" = use English
  const [strings, help, addHelp] = await Promise.all([
    fetchText(`lang/${code}/strings.json`).then(JSON.parse).catch((err) => {
      console.error(`CamWall: lang/${code}/strings.json could not be used:`, err.message);
      return {};
    }),
    optional("help.html"),
    optional("add-camera.html"),
  ]);
  return (packs[code] = { strings, help, addHelp });
}

async function loadLanguageList() {
  try { languages = JSON.parse(await fetchText("lang/languages.json")); } catch { languages = []; }
  if (!languages.length) languages = [{ code: "pl", label: "PL" }, { code: "en", label: "EN" }];
}

function initialLanguage() {
  const codes = languages.map((l) => l.code);
  const saved = storageGet("camwall.lang");
  if (codes.includes(saved)) return saved;
  for (const pref of navigator.languages || [navigator.language || ""]) {
    const match = codes.find((c) => pref.toLowerCase().startsWith(c.toLowerCase()));
    if (match) return match;
  }
  return codes.includes(BASE_LANG) ? BASE_LANG : codes[0];
}

function t(key, vars = {}) {
  const s = texts[key] ?? BUILTIN[key] ?? key;
  return String(s).replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");
}

// A failure reason is { key, vars } (translated when shown) or { raw } (go2rtc text).
function reasonText(r) {
  return r.key ? t(r.key, r.vars) : r.raw;
}

// ---------------------------------------------------------------------------
// WebRTC player for one go2rtc stream, with automatic reconnect.
// ---------------------------------------------------------------------------

const RETRY_MIN_MS = 2000;
const RETRY_MAX_MS = 30000;
const CONNECT_TIMEOUT_MS = 15000;
const FROZEN_AFTER_MS = 10000;

function iceGathered(pc, timeoutMs) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, timeoutMs);
    pc.addEventListener("icegatheringstatechange", () => {
      if (pc.iceGatheringState === "complete") { clearTimeout(timer); resolve(); }
    });
  });
}

class Player {
  constructor(video, onState) {
    this.video = video;
    this.onState = onState;
    this.src = null;
    this.pc = null;
    this.active = false;
    this.state = "idle";
    this.lastError = null;   // reason of the last failure, shown while retrying
    this.retryIn = 0;        // seconds until the next attempt
    this.retryDelay = RETRY_MIN_MS;

    video.addEventListener("playing", () => {
      if (!this.pc) return;
      clearTimeout(this.connectTimer);
      this.retryDelay = RETRY_MIN_MS;
      this.lastError = null;
      this.setState("live");
      this.watchFrozen();
    });
  }

  play(src) {
    if (this.active && this.src === src) return;
    this.src = src;
    this.active = true;
    this.retryDelay = RETRY_MIN_MS;
    this.connect();
  }

  stop() {
    this.active = false;
    this.teardown();
    this.setState("idle");
  }

  setState(state) {
    this.state = state;
    this.onState(this);
  }

  teardown() {
    clearTimeout(this.retryTimer);
    clearTimeout(this.connectTimer);
    clearTimeout(this.disconnectTimer);
    clearInterval(this.frozenTimer);
    if (this.pc) {
      this.pc.ontrack = null;
      this.pc.onconnectionstatechange = null;
      this.pc.close();
      this.pc = null;
    }
    this.video.srcObject = null;
  }

  async connect() {
    this.teardown();
    if (!this.active) return;
    this.setState("connecting");

    const pc = (this.pc = new RTCPeerConnection());
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.ontrack = (e) => {
      this.video.srcObject = e.streams[0] || new MediaStream([e.track]);
      this.video.play().catch(() => {});
    };
    pc.onconnectionstatechange = () => {
      if (pc !== this.pc) return;
      const s = pc.connectionState;
      if (s === "failed" || s === "closed") {
        this.fail({ key: "errLost" });
      } else if (s === "disconnected") {
        // Often recovers by itself on Wi-Fi; give it a few seconds.
        clearTimeout(this.disconnectTimer);
        this.disconnectTimer = setTimeout(() => {
          if (pc === this.pc && pc.connectionState !== "connected") this.fail({ key: "errLost" });
        }, 4000);
      }
    };
    this.connectTimer = setTimeout(() => {
      if (pc === this.pc && this.state !== "live") this.fail({ key: "errNoResponse" });
    }, CONNECT_TIMEOUT_MS);

    try {
      await pc.setLocalDescription(await pc.createOffer());
      await iceGathered(pc, 1500);
      const res = await fetch(`api/webrtc?src=${encodeURIComponent(this.src)}`, {
        method: "POST",
        headers: { "Content-Type": "application/sdp" },
        body: pc.localDescription.sdp,
      });
      if (pc !== this.pc) return;
      if (!res.ok) {
        const body = (await res.text()).trim();
        throw body && !body.startsWith("<")
          ? new Error(body)
          : { reason: { key: "errHttp", vars: { code: res.status } } };
      }
      const sdp = await res.text();
      if (pc !== this.pc) return;
      await pc.setRemoteDescription({ type: "answer", sdp });
    } catch (err) {
      if (pc === this.pc) this.fail(failureReason(err));
    }
  }

  // Reconnect if the picture stops moving while the connection looks fine.
  watchFrozen() {
    clearInterval(this.frozenTimer);
    let lastTime = -1;
    let lastChange = Date.now();
    this.frozenTimer = setInterval(() => {
      const time = this.video.currentTime;
      if (time !== lastTime) { lastTime = time; lastChange = Date.now(); }
      else if (Date.now() - lastChange > FROZEN_AFTER_MS) this.fail({ key: "errFrozen" });
    }, 2000);
  }

  fail(reason) {
    if (!this.active) return;
    this.teardown();
    this.lastError = reason;
    const delay = this.retryDelay;
    this.retryDelay = Math.min(delay * 2, RETRY_MAX_MS);
    this.retryIn = Math.round(delay / 1000);
    this.setState("offline");
    this.retryTimer = setTimeout(() => this.connect(), delay);
  }
}

function failureReason(err) {
  if (err && err.reason) return err.reason;
  const msg = String(err && err.message || err);
  if (/failed to fetch|networkerror/i.test(msg)) return { key: "errServerDown" };
  if (/404/.test(msg)) return { key: "errNotFound" };
  if (/dial tcp|i\/o timeout|connection refused|no route/i.test(msg)) return { key: "errUnreachable" };
  if (/401|unauthorized/i.test(msg)) return { key: "errAuth" };
  if (/codec/i.test(msg)) return { key: "errCodec" };
  return { raw: msg.length > 80 ? msg.slice(0, 77) + "…" : msg };
}

function renderState(el, player) {
  const state = player.state;
  el.dataset.state = state;
  el.querySelector(".pill-text").textContent = t(state);
  let msg = "";
  if (state === "connecting") {
    msg = player.lastError ? t("msgReconnecting", { reason: reasonText(player.lastError) }) : t("msgConnecting");
  } else if (state === "offline") {
    msg = t("msgOffline", { reason: reasonText(player.lastError), s: player.retryIn });
  }
  el.querySelector(".state-msg").textContent = msg;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const wall = document.getElementById("wall");
const filtersEl = document.getElementById("filters");
const liveCountEl = document.getElementById("liveCount");
const viewer = document.getElementById("viewer");
const addHelp = document.getElementById("addHelp");
const mainHelp = document.getElementById("mainHelp");
const aboutDialog = document.getElementById("about");
const weatherDialog = document.getElementById("weatherDialog");

let cameras = [];
let tiles = [];          // { cam, el, player }
let addTile = null;
let currentGroup = "";   // "" = all
let viewerIndex = -1;
let loadError = null;    // string key when cameras.json could not be loaded

const viewerPlayer = new Player(viewer.querySelector("video"), (p) => renderState(viewer, p));

function metaText(cam) {
  return [cam.group, cam.model].filter(Boolean).join(" · ");
}

function buildTiles() {
  const template = document.getElementById("tileTemplate");
  tiles = cameras.map((cam, i) => {
    const el = template.content.firstElementChild.cloneNode(true);
    el.querySelector(".tile-name").textContent = cam.name;
    el.querySelector(".tile-meta").textContent = metaText(cam);
    el.setAttribute("aria-label", cam.name);
    const tile = { cam, el };
    tile.player = new Player(el.querySelector("video"), (p) => {
      renderState(el, p);
      updateLiveCount();
    });
    el.addEventListener("dblclick", () => openViewer(i));
    // Double-tap on touch screens (not every mobile browser sends dblclick).
    let lastTap = 0;
    el.addEventListener("pointerup", (e) => {
      if (e.pointerType !== "touch") return;
      if (e.timeStamp - lastTap < 350) { lastTap = 0; openViewer(i); } else lastTap = e.timeStamp;
    });
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openViewer(i); } });
    wall.appendChild(el);
    return tile;
  });

  addTile = document.createElement("button");
  addTile.className = "tile tile-add";
  addTile.innerHTML = '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg><span class="add-label"></span>';
  addTile.addEventListener("click", () => addHelp.showModal());
  wall.appendChild(addTile);
}

function buildFilters() {
  const groups = [...new Set(cameras.map((c) => c.group).filter(Boolean))];
  if (groups.length < 2) { filtersEl.hidden = true; return; }
  const options = [["", cameras.length], ...groups.map((g) => [g, cameras.filter((c) => c.group === g).length])];
  for (const [value, count] of options) {
    const b = document.createElement("button");
    b.dataset.group = value;
    b.innerHTML = `<span class="label"></span><span class="count">${count}</span>`;
    b.firstChild.textContent = value;  // the "All" label is set in applyLanguage()
    b.addEventListener("click", () => setGroup(value));
    filtersEl.appendChild(b);
  }
  const saved = storageGet("camwall.group");
  setGroup(saved !== null && (saved === "" || groups.includes(saved)) ? saved : "");
}

function visibleTiles() {
  return tiles.filter((tile) => !currentGroup || tile.cam.group === currentGroup);
}

function setGroup(group) {
  currentGroup = group;
  storageSet("camwall.group", group);
  for (const b of filtersEl.children) b.setAttribute("aria-pressed", String(b.dataset.group === group));
  for (const tile of tiles) tile.el.hidden = !!group && tile.cam.group !== group;
  addTile.hidden = !!group;
  syncPlayback();
  layout();
}

// Only stream what is on screen: visible tiles, while the tab is visible
// and the full-size viewer is closed (cheap cameras allow few connections).
function syncPlayback() {
  const pageVisible = document.visibilityState === "visible";
  const showGrid = pageVisible && !viewer.open;
  for (const tile of tiles) {
    if (showGrid && !tile.el.hidden) tile.player.play(tile.cam.gridStream);
    else tile.player.stop();
  }
  if (viewer.open && pageVisible && viewerIndex >= 0) viewerPlayer.play(cameras[viewerIndex].stream);
  else viewerPlayer.stop();
  updateLiveCount();
}

function updateLiveCount() {
  const shown = visibleTiles();
  const live = shown.filter((tile) => tile.player.state === "live").length;
  liveCountEl.querySelector(".lc-num").textContent = `${live} / ${shown.length}`;
  liveCountEl.querySelector(".lc-word").textContent = t("liveWord");
  liveCountEl.classList.toggle("partial", live > 0 && live < shown.length);
  liveCountEl.classList.toggle("none", live === 0 && shown.length > 0);
}

// Pick the column count that makes 16:9 tiles as large as possible
// while the whole wall fits on one screen.
function layout() {
  // Group buttons squeezed by the other top-bar controls? Give them their own row.
  const topbar = document.querySelector(".topbar");
  topbar.classList.remove("stacked");
  if (!filtersEl.hidden && filtersEl.scrollWidth > filtersEl.clientWidth + 1) topbar.classList.add("stacked");

  if (!addTile) return;
  const n = visibleTiles().length + (addTile.hidden ? 0 : 1);
  const style = getComputedStyle(wall);
  const gap = parseFloat(style.columnGap) || 12;
  const padX = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
  const padY = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
  const W = document.documentElement.clientWidth - padX;
  const H = window.innerHeight - document.querySelector(".topbar").offsetHeight - padY;

  if (W < 640 || n === 0) {
    wall.style.setProperty("--cols", "1");
    wall.style.removeProperty("--wall-w");
    return;
  }
  let best = { cols: 1, w: 0 };
  for (let cols = 1; cols <= n; cols++) {
    const rows = Math.ceil(n / cols);
    let w = (W - gap * (cols - 1)) / cols;
    const h = w * 9 / 16;
    if (rows * h + gap * (rows - 1) > H) w = ((H - gap * (rows - 1)) / rows) * 16 / 9;
    if (w > best.w) best = { cols, w };
  }
  wall.style.setProperty("--cols", String(best.cols));
  wall.style.setProperty("--wall-w", `${Math.floor(best.w * best.cols + gap * (best.cols - 1) + padX)}px`);
}

// ---------- language ----------

const langSwitch = document.getElementById("langSwitch");

function buildLangSwitch() {
  langSwitch.hidden = languages.length < 2;
  for (const l of languages) {
    const b = document.createElement("button");
    b.dataset.setLang = l.code;
    b.lang = l.code;
    b.textContent = l.label || l.code.toUpperCase();
    if (l.name) b.title = l.name;
    b.addEventListener("click", () => applyLanguage(l.code));
    langSwitch.appendChild(b);
  }
}

async function applyLanguage(newLang) {
  const [base, pack] = await Promise.all([loadPack(BASE_LANG), loadPack(newLang)]);
  lang = newLang;
  texts = { ...base.strings, ...pack.strings };
  locale = languages.find((l) => l.code === lang)?.locale || pack.strings._meta?.locale || lang;
  storageSet("camwall.lang", lang);
  document.documentElement.lang = lang;

  document.getElementById("mainHelpBody").innerHTML = pack.help || base.help;
  document.getElementById("addHelpBody").innerHTML = pack.addHelp || base.addHelp;
  for (const el of document.querySelectorAll("[data-i18n-title]")) {
    const text = t(el.dataset.i18nTitle);
    el.title = text;
    el.setAttribute("aria-label", text);
  }
  for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const b of langSwitch.children) b.setAttribute("aria-pressed", String(b.dataset.setLang === lang));
  viewer.setAttribute("aria-label", t("camera"));

  const allButton = filtersEl.querySelector('[data-group=""] .label');
  if (allButton) allButton.textContent = t("all");
  if (addTile) addTile.querySelector(".add-label").textContent = t("addCamera");
  for (const tile of tiles) {
    tile.el.title = t("tileHint");
    renderState(tile.el, tile.player);
  }
  renderState(viewer, viewerPlayer);
  updateLiveCount();
  if (loadError) wall.firstChild.textContent = t(loadError);
  renderWeather();
  tick();
  layout();
  document.body.classList.remove("loading");
}

// ---------- full-size viewer ----------

let idleTimer;
function wakeViewer() {
  viewer.classList.remove("idle");
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => viewer.classList.add("idle"), 3000);
}

function openViewer(index) {
  viewerIndex = index;
  const cam = cameras[index];
  document.getElementById("viewerName").textContent = cam.name;
  document.getElementById("viewerMeta").textContent = metaText(cam);
  if (!viewer.open) viewer.showModal();
  wakeViewer();
  syncPlayback();
}

function stepViewer(dir) {
  const shown = visibleTiles().map((tile) => tiles.indexOf(tile));
  if (!shown.length) return;
  const pos = shown.indexOf(viewerIndex);
  openViewer(shown[(pos + dir + shown.length) % shown.length]);
}

function toggleFullscreen(el) {
  if (document.fullscreenElement) document.exitFullscreen();
  else el.requestFullscreen?.().catch(() => {});
}

viewer.addEventListener("close", () => {
  if (document.fullscreenElement === viewer) document.exitFullscreen();
  viewerIndex = -1;
  syncPlayback();
});
viewer.addEventListener("mousemove", wakeViewer);
viewer.addEventListener("click", (e) => { if (e.target === viewer || e.target.tagName === "VIDEO") wakeViewer(); });
// Double-click anywhere on the picture closes the enlarged view (but not on the buttons).
viewer.addEventListener("dblclick", (e) => { if (!e.target.closest(".viewer-actions")) viewer.close(); });
document.getElementById("viewerClose").addEventListener("click", () => viewer.close());
document.getElementById("viewerPrev").addEventListener("click", () => stepViewer(-1));
document.getElementById("viewerNext").addEventListener("click", () => stepViewer(1));
document.getElementById("viewerFullscreen").addEventListener("click", () => toggleFullscreen(viewer));
document.getElementById("wallFullscreen").addEventListener("click", () => toggleFullscreen(document.documentElement));

function openHelp() {
  mainHelp.showModal();
  mainHelp.scrollTop = 0;  // the "Close" button gets focus and would scroll to the bottom
}
document.getElementById("helpButton").addEventListener("click", openHelp);
document.getElementById("aboutButton").addEventListener("click", () => aboutDialog.showModal());
document.getElementById("weather").addEventListener("click", () => weatherDialog.showModal());

document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea") || addHelp.open || mainHelp.open || aboutDialog.open || weatherDialog.open) return;
  if (e.key === "?" && !viewer.open) { openHelp(); return; }
  if ((e.key === "w" || e.key === "W") && !viewer.open && weather) { weatherDialog.showModal(); return; }
  if (viewer.open && e.key === "ArrowLeft") stepViewer(-1);
  else if (viewer.open && e.key === "ArrowRight") stepViewer(1);
  else if (e.key === "f" || e.key === "F") toggleFullscreen(viewer.open ? viewer : document.documentElement);
  else return;
  wakeViewer();
});

document.addEventListener("visibilitychange", syncPlayback);
window.addEventListener("resize", layout);

// ---------- weather ----------
// camwall.py reads the TH16-R2 station and its RF sensors into weather.json
// (see weather.py); without a station the file is absent and nothing shows.

const WEATHER_REFRESH_MS = 30000;
const weatherEl = document.getElementById("weather");
let weather = null;          // contents of weather.json
let weatherFailed = false;   // last fetch failed: keep the values, shown as old

async function loadWeather() {
  try {
    const res = await fetch("weather.json", { cache: "no-store" });
    if (res.status === 404) { weather = null; weatherFailed = false; }
    else if (!res.ok) throw new Error(`HTTP ${res.status}`);
    else { weather = await res.json(); weatherFailed = false; }
  } catch {
    weatherFailed = true;
  }
  renderWeather();
}

function formatNumber(value, digits) {
  try {
    return value.toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  } catch {  // invalid "locale" in a strings.json
    return value.toFixed(digits);
  }
}

function formatTime(iso) {
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  const options = sameDay ? { hour: "2-digit", minute: "2-digit" } : { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" };
  try { return d.toLocaleString(locale, options); } catch { return d.toLocaleString(undefined, options); }
}

function isStale(device) {
  if (device.stale || weatherFailed || device.temperature == null) return true;
  // camwall.py stopped writing the file (e.g. stopped): readings are getting old.
  return Date.now() - Date.parse(weather.updated) > weather.staleAfter * 1000;
}

function weatherDetails(device) {
  const parts = [device.name];
  if (device.humidity != null) parts.push(`${t("wxHumidity")} ${formatNumber(device.humidity, 0)}%`);
  if (device.battery != null) parts.push(`${t("wxBattery")} ${formatNumber(device.battery, 0)}%`);
  if (device.time) parts.push(t("wxRead", { time: formatTime(device.time), source: t(device.source === "lan" ? "wxSourceLan" : "wxSourceCloud") }));
  if (isStale(device)) parts.push(t(device.time ? "wxStale" : "wxNoData"));
  return parts.join(" · ");
}

function createEl(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function renderWeather() {
  const devices = weather?.devices || [];
  weatherEl.hidden = !devices.length;
  document.querySelector(".topbar").classList.toggle("has-weather", !!devices.length);  // phones: own row
  if (!devices.length) {
    if (weatherDialog.open) weatherDialog.close();
    layout();
    return;
  }
  weatherEl.title = t("weather");

  const items = document.getElementById("weatherItems");
  items.replaceChildren(...devices.map((d) => {
    const item = createEl("span", "wx-item");
    item.classList.toggle("stale", isStale(d));
    item.title = weatherDetails(d);
    item.append(
      createEl("span", "wx-name", d.name),
      createEl("span", "wx-temp", d.temperature == null ? "–" : `${formatNumber(d.temperature, 1)}°`),
    );
    if (d.humidity != null) item.append(createEl("span", "wx-hum", `${formatNumber(d.humidity, 0)}%`));
    return item;
  }));

  const cards = document.getElementById("weatherCards");
  cards.replaceChildren(...devices.map((d) => {
    const stale = isStale(d);
    const card = createEl("section", "wx-card");
    card.classList.toggle("stale", stale);
    card.append(
      createEl("div", "wx-card-name", d.name),
      createEl("div", "wx-card-role", t(d.main ? "wxMain" : "wxSensor")),
      createEl("div", "wx-card-temp", d.temperature == null ? "–" : `${formatNumber(d.temperature, 1)} °C`),
    );
    const dl = createEl("dl");
    if (d.humidity != null) dl.append(createEl("dt", "", t("wxHumidity")), createEl("dd", "", `${formatNumber(d.humidity, 0)} %`));
    if (d.battery != null) {
      dl.append(createEl("dt", "", t("wxBattery")), createEl("dd", d.battery < 20 ? "low" : "", `${formatNumber(d.battery, 0)} %`));
    }
    card.append(dl);
    const when = d.time
      ? t("wxRead", { time: formatTime(d.time), source: t(d.source === "lan" ? "wxSourceLan" : "wxSourceCloud") })
      : t("wxNoData");
    const time = createEl("p", "wx-card-time", stale && d.time ? `${when} · ${t("wxStale")}` : when);
    time.classList.toggle("stale", stale && !!d.time);
    card.append(time);
    return card;
  }));
  layout();  // the top bar got wider or narrower
}

setInterval(loadWeather, WEATHER_REFRESH_MS);

// ---------- clock ----------

const clockEl = document.getElementById("clock");
function tick() {
  const now = new Date();
  clockEl.dateTime = now.toISOString();
  const options = { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" };
  try {
    clockEl.textContent = now.toLocaleString(locale, options);
  } catch {  // invalid "locale" in a strings.json
    clockEl.textContent = now.toLocaleString(undefined, options);
  }
}
setInterval(tick, 1000);

// ---------- start ----------

async function init() {
  await loadLanguageList();
  buildLangSwitch();
  lang = initialLanguage();
  await applyLanguage(lang);
  loadWeather();
  try {
    const res = await fetch("cameras.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    cameras = await res.json();
  } catch (err) {
    loadError = location.protocol === "file:" ? "loadFailFile" : "loadFail";
    wall.innerHTML = '<p class="tile-meta" style="grid-column:1/-1;text-align:center;padding:40px;font-size:15px"></p>';
    await applyLanguage(lang);
    return;
  }
  buildTiles();
  buildFilters();
  if (filtersEl.hidden) setGroup("");
  applyLanguage(lang);
}

init();
