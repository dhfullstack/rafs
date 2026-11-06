"use strict";
// rafs dashboard. All data reaches the DOM through textContent (never innerHTML).

const COLORS = { jev: "var(--s-jev)", baseline: "var(--s-baseline)", random: "var(--s-random)", quant: "var(--s-quant)",
  memes: "var(--s-memes)", memes_top: "var(--s-memes-top)" };
const LABELS = { jev: "Jev", baseline: "Baseline", random: "Random", quant: "Quant 12-1",
  memes: "Memes Grok+Jev", memes_top: "Memes top SCAN" };
const MEME = new Set(["memes", "memes_top"]);
const POLL_MS = 5000;
const st = { csrf: null, env: localStorage.getItem("gb_env") || "", tab: "overview", data: null, timer: null,
  decider: "jev", perfBook: "all", book: null, histLimit: 60, animate: true };

// ---------------------------------------------------------------- utilidades
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v; // CSSOM: permitido pela CSP (atributo inline não é)
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const svgNS = "http://www.w3.org/2000/svg";
function s(tag, attrs) {
  const el = document.createElementNS(svgNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  return el;
}
const ICONS = {
  up: "M7 17L17 7M9 7h8v8", down: "M7 7l10 10M17 9v8H9", hold: "M6 12h12", check: "M5 12.5l4.5 4.5L19 7",
  x: "M6 6l12 12M18 6L6 18", clock: "M12 7v5l3 2M12 21a9 9 0 110-18 9 9 0 010 18z", alert: "M12 8v5M12 16.5v.01",
  bolt: "M13 3L5 13.5h6L10 21l8-10.5h-6L13 3z", info: "M12 11v5M12 7.5v.01",
};
function icon(name, cls = "") {
  const svg = s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
  svg.append(s("path", { d: ICONS[name] }));
  return h("span", { class: `ico ${cls}` }, svg);
}
const nf2 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
// moeda de cotação do modo exibido (BRL no PAPER; USDT se a testnet usar pares em USDT)
const CUR = { BRL: "R$ ", USD: "$", USDT: "$", USDC: "$" };
const cur = () => { const q = st.data && st.data.quote; return q ? (CUR[q] ?? q + " ") : "$"; };
const brl = (v) => (v == null ? "—" : (v < 0 ? "−" : "") + cur() + nf2.format(Math.abs(v)));
const usd = (v) => (v == null ? "—" : "$" + nf2.format(v)); // banca simulada dos memes
const money = (book) => (MEME.has(book) ? usd : brl);
const num = (v, d = 2) => (v == null ? "—" : new Intl.NumberFormat("en-US", { maximumFractionDigits: d }).format(v));
// preço de memecoin tem muitos zeros: abaixo de 1, 4 dígitos significativos
const px = (v) => (v == null ? "—" : Math.abs(v) >= 1 ? num(v) : new Intl.NumberFormat("en-US", { maximumSignificantDigits: 4 }).format(v));
const compact = (v) => (v == null ? "—" : new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v));
const pct = (v, d = 2) => (v == null ? "—" : num(v, d) + "%");
const tone = (v) => (v > 0.0049 ? "pos" : v < -0.0049 ? "neg" : "");
function signedText(v, fmt = brl) {
  if (v == null) return "—";
  const sign = v > 0.0049 ? "+" : v < -0.0049 ? "−" : "";
  return sign + fmt(Math.abs(v));
}
function signed(v, fmt = brl) {
  if (v == null) return h("span", { class: "muted" }, "—");
  return h("span", { class: tone(v) || "muted" }, signedText(v, fmt));
}
const dt = (ms) => (ms ? new Date(ms).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—");
function ago(ms) {
  if (!ms) return "never";
  const sec = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)} h ago`;
  return `${Math.round(sec / 86400)} d ago`;
}
const swatch = (book) => h("span", { class: "swatch", style: `background:${COLORS[book] || "var(--muted)"}` });
const label = (book) => LABELS[book] || book;
const who = (book) => h("span", { class: "nowrap" }, swatch(book), " ", label(book));
const sep = () => h("span", { "aria-hidden": "true" }, "·");
function head(title, sub, extra) {
  return h("div", { class: "card-head" }, h("div", {}, h("h2", {}, title), sub ? h("div", { class: "sub" }, sub) : null), extra || null);
}
function card(title, sub, ...body) {
  return h("section", { class: "card" }, head(title, sub), ...body);
}
function flush(title, sub, extra, ...body) {
  return h("section", { class: "card flush" }, head(title, sub, extra), ...body);
}
function emptyState(title, text) {
  return h("div", { class: "empty" }, h("strong", {}, title), text ? h("span", {}, text) : null);
}
function list(items, empty) {
  return items.length ? h("ul", { class: "list" }, items) : empty;
}
function item({ ico, title, sub, end, endSub, note, onclick }) {
  return h("li", { onclick, style: onclick ? "cursor:pointer" : null },
    ico,
    h("div", { style: "min-width:0" }, h("div", { class: "li-title" }, title), sub ? h("div", { class: "li-sub" }, sub) : null),
    h("div", { class: "li-end" }, h("div", { class: "big" }, end), endSub ? h("div", { class: "li-sub" }, endSub) : null),
    note ? h("div", { class: "li-note" }, note) : null);
}
function table(cols, rows, empty) {
  if (!rows.length) return empty || emptyState("Nothing yet", null);
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, cols.map((c) => h("th", { class: c.num ? "num" : null }, c.t)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, cols.map((c) => h("td", { class: c.num ? "num" : null }, c.v(r))))))));
}
function seg(options, active, onpick) {
  return h("div", { class: "seg", role: "tablist" }, options.map(([value, text, book]) =>
    h("button", { class: value === active ? "active" : null, role: "tab", "aria-selected": String(value === active),
      onclick: () => onpick(value) }, book ? swatch(book) : null, text)));
}
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 3500);
}

// ---------------------------------------------------------------- API
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (st.csrf) headers["X-CSRF"] = st.csrf;
  const r = await fetch(path, { credentials: "same-origin", ...opts, headers });
  const body = await r.json().catch(() => ({}));
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("session expired"); }
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body;
}
async function load() {
  try {
    const q = st.env ? `?env=${encodeURIComponent(st.env)}` : "";
    st.data = await api(`/api/state${q}`);
    if (!st.env) st.env = st.data.mode;
    if (st.hovering) st.pending = true; // não apaga o tooltip enquanto o dedo/mouse está no gráfico
    else render();
  } catch (e) {
    if (e.message !== "session expired") setStatus("bad", "Offline", false);
  }
}
function startPolling() {
  clearInterval(st.timer);
  st.timer = setInterval(() => { if (!document.hidden) load(); }, POLL_MS);
}
function greet() {
  const now = new Date();
  const hr = now.getHours();
  document.getElementById("greet").textContent =
    hr >= 5 && hr < 12 ? "Good morning." : hr >= 12 && hr < 18 ? "Good afternoon." : "Good evening.";
  const day = now.toLocaleDateString("en-US", { weekday: "short", day: "numeric", month: "short" });
  const time = now.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  document.getElementById("clock").textContent = `${day} · ${time}`;
}
function showLogin() {
  clearInterval(st.timer);
  document.body.classList.remove("in-app");
  greet();
  document.getElementById("app").hidden = true;
  document.getElementById("login").hidden = false;
  document.getElementById("pw").focus();
}
function showApp() {
  document.body.classList.add("in-app");
  document.getElementById("login").hidden = true;
  document.getElementById("app").hidden = false;
  document.getElementById("main").replaceChildren(
    h("div", { class: "skel", style: "height:150px" }), h("div", { class: "skel", style: "height:280px" }),
    h("div", { class: "skel", style: "height:220px" }));
  load();
  startPolling();
}

// ---------------------------------------------------------------- confirmação (sem prompt() do navegador)
function ask({ title, text, word, okLabel, danger }) {
  const dlg = document.getElementById("confirm");
  const input = document.getElementById("confirm-word");
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-text").textContent = text;
  document.getElementById("confirm-word-wrap").hidden = !word;
  document.getElementById("confirm-word-label").textContent = word || "";
  input.value = "";
  const ok = document.getElementById("confirm-ok");
  ok.textContent = okLabel || "Confirm";
  ok.className = `btn ${danger ? "danger" : "primary"}`;
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => {
      if (dlg.returnValue !== "ok") return resolve(null);
      const typed = input.value.trim().toUpperCase();
      if (word && typed !== word) { toast("Cancelled: the word does not match."); return resolve(null); }
      resolve(typed);
    }, { once: true });
    dlg.returnValue = "";
    dlg.showModal();
    (word ? input : ok).focus();
  });
}

// ---------------------------------------------------------------- gráfico (SVG, uma escala só)
function equityChart(books, { height = 260, focus = null, wide = false, fmt = brl } = {}) {
  const wrap = h("div", { class: "chart" });
  const series = books.filter((b) => b.curve && b.curve.length);
  if (!series.length || series.every((b) => b.curve.length < 2)) {
    wrap.append(emptyState("No equity curve yet", "It shows up after the first processed candles."));
    return wrap;
  }
  st.hovering = false;
  // desenha perto da largura real do card: o texto fica legível no iPhone em vez de encolher junto com o SVG
  const vw = window.innerWidth, desk = vw >= 900, inner = Math.min(vw, 1180) - (desk ? 48 : 28) - 38;
  const W = Math.round(Math.max(280, wide || !desk ? inner : (inner - 18) * (1.75 / 2.75))), narrow = W < 500;
  // rótulo no fim da linha: a margem direita cresce com o nome mais longo ("Memes Grok+Jev")
  const longest = Math.max(...series.map((b) => label(b.name).length));
  const H = height, padL = narrow ? 44 : 64, padR = Math.max(narrow ? 64 : 76, 14 + longest * 6.4), padT = 12, padB = 26;
  const all = series.flatMap((b) => b.curve);
  const initial = series[0].initial;
  let x0 = Math.min(...all.map((p) => p[0])), x1 = Math.max(...all.map((p) => p[0]));
  let y0 = Math.min(initial, ...all.map((p) => p[1])), y1 = Math.max(initial, ...all.map((p) => p[1]));
  const padY = Math.max((y1 - y0) * 0.1, 0.5);
  const raw = (y1 - y0 + 2 * padY) / 4, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw);
  y0 = Math.floor((y0 - padY) / step) * step; y1 = Math.ceil((y1 + padY) / step) * step;
  const ticks = Math.round((y1 - y0) / step);
  if (x1 === x0) x1 = x0 + 1;
  const X = (t) => padL + ((t - x0) / (x1 - x0)) * (W - padL - padR);
  const Y = (v) => padT + (1 - (v - y0) / (y1 - y0)) * (H - padT - padB);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img",
    "aria-label": "Simulated equity curve per wallet" });
  for (let i = 0; i <= ticks; i++) {
    const v = y0 + step * i, y = Y(v);
    svg.append(s("line", { x1: padL, x2: W - padR, y1: y, y2: y, stroke: "var(--grid)", "stroke-width": 1, "vector-effect": "non-scaling-stroke" }));
    const t = s("text", { x: padL - 10, y: y + 4, "text-anchor": "end", fill: "var(--muted)", "font-size": 11 });
    t.textContent = step >= 1 ? Math.round(v).toLocaleString("en-US") : nf2.format(v);
    svg.append(t);
  }
  svg.append(s("line", { x1: padL, x2: W - padR, y1: Y(initial), y2: Y(initial), stroke: "var(--axis)", "stroke-width": 1,
    "stroke-dasharray": "3 4", "vector-effect": "non-scaling-stroke" }));
  for (const frac of [0, 0.5, 1]) {
    const t = s("text", { x: padL + frac * (W - padL - padR), y: H - 6, fill: "var(--muted)", "font-size": 11,
      "text-anchor": frac === 0 ? "start" : frac === 1 ? "end" : "middle" });
    t.textContent = dt(x0 + frac * (x1 - x0));
    svg.append(t);
  }
  // área suave só sob a carteira em foco; demais linhas recuam
  const defs = s("defs", {});
  svg.append(defs);
  const ends = [];
  const ordered = focus ? [...series.filter((b) => b.name !== focus), ...series.filter((b) => b.name === focus)] : series;
  for (const b of ordered) {
    const pts = b.curve.map((p) => [X(p[0]), Y(p[1])]);
    const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
    const color = COLORS[b.name] || "var(--muted)";
    const isFocus = !focus || b.name === focus;
    if (focus && b.name === focus) {
      const id = `g-${b.name}`;
      const grad = s("linearGradient", { id, x1: 0, y1: 0, x2: 0, y2: 1 });
      grad.append(s("stop", { offset: "0%", "stop-color": color, "stop-opacity": 0.22 }),
        s("stop", { offset: "100%", "stop-color": color, "stop-opacity": 0 }));
      defs.append(grad);
      svg.append(s("path", { d: `${d}L${pts[pts.length - 1][0].toFixed(1)},${H - padB}L${pts[0][0].toFixed(1)},${H - padB}Z`,
        fill: `url(#${id})`, stroke: "none" }));
    }
    svg.append(s("path", { d, fill: "none", stroke: color, "stroke-width": isFocus ? 2 : 1.5, opacity: isFocus ? 1 : 0.5,
      "stroke-linejoin": "round", "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" }));
    const last = b.curve[b.curve.length - 1];
    ends.push({ b, y: Y(last[1]), isFocus });
  }
  ends.sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;
  for (const e of ends) {
    const t = s("text", { x: W - padR + 8, y: e.y + 4, fill: e.isFocus ? "var(--ink)" : "var(--muted)", "font-size": 11,
      "font-weight": e.isFocus ? 600 : 400 });
    t.textContent = label(e.b.name);
    svg.append(t);
  }
  // crosshair + tooltip
  const cross = s("line", { y1: padT, y2: H - padB, stroke: "var(--axis)", "stroke-width": 1, "vector-effect": "non-scaling-stroke", visibility: "hidden" });
  svg.append(cross);
  const dots = series.map((b) => { const c = s("circle", { r: 4, fill: COLORS[b.name], stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" }); svg.append(c); return c; });
  const tip = h("div", { class: "tip", hidden: true });
  const legend = h("div", { class: "legend" }, series.map((b) => h("span", {}, swatch(b.name), label(b.name))));
  wrap.append(svg, tip);
  function nearest(curve, t) {
    let best = curve[0];
    for (const p of curve) { if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p; }
    return best;
  }
  function move(ev) {
    st.hovering = true;
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    if (px < padL || px > W - padR) return leave();
    const t = x0 + ((px - padL) / (W - padL - padR)) * (x1 - x0);
    const pts = series.map((b) => nearest(b.curve, t));
    const tx = pts[0][0];
    cross.setAttribute("x1", X(tx)); cross.setAttribute("x2", X(tx)); cross.setAttribute("visibility", "visible");
    pts.forEach((p, i) => { dots[i].setAttribute("cx", X(p[0])); dots[i].setAttribute("cy", Y(p[1])); dots[i].setAttribute("visibility", "visible"); });
    tip.replaceChildren(h("div", { class: "t" }, dt(tx)),
      ...series.map((b, i) => h("div", { class: "row" }, h("span", {}, swatch(b.name), label(b.name)), h("strong", {}, fmt(pts[i][1])))));
    tip.hidden = false;
    const left = ((ev.clientX - r.left) / r.width) > 0.55 ? (ev.clientX - r.left) - 176 : (ev.clientX - r.left) + 14;
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = "8px";
  }
  function leave() {
    st.hovering = false;
    if (st.pending) { st.pending = false; setTimeout(render, 0); }
    cross.setAttribute("visibility", "hidden");
    dots.forEach((d) => d.setAttribute("visibility", "hidden"));
    tip.hidden = true;
  }
  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerdown", move);
  svg.addEventListener("pointerleave", leave);
  svg.style.height = `${H}px`;
  wrap.legend = legend;
  return wrap;
}

// ---------------------------------------------------------------- more charts: daily P&L, returns, drawdown
// width close to the real card, same rule as equityChart: "main"/"side" = the two columns of .split on desktop
function chartWidth(col = "full") {
  const vw = window.innerWidth, desk = vw >= 900, inner = Math.min(vw, 1180) - (desk ? 48 : 28) - 38;
  if (!desk || col === "full") return Math.round(Math.max(280, inner));
  return Math.round(Math.max(240, (inner - 18) * (col === "main" ? 1.75 / 2.75 : 1 / 2.75)));
}
function niceScale(lo, hi, n = 4) {
  const raw = Math.max(hi - lo, 1e-9) / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw);
  return { lo: Math.floor(lo / step + 1e-9) * step, hi: Math.ceil(hi / step - 1e-9) * step, step };
}
function hoverTip(wrap, svg, W, onIndex, count, padL, padR) {
  const tip = h("div", { class: "tip", hidden: true });
  wrap.append(tip);
  const leave = () => {
    st.hovering = false;
    if (st.pending) { st.pending = false; setTimeout(render, 0); }
    tip.hidden = true; onIndex(-1);
  };
  const move = (ev) => {
    st.hovering = true;
    const r = svg.getBoundingClientRect();
    const x = ((ev.clientX - r.left) / r.width) * W;
    if (x < padL || x > W - padR) return leave();
    const i = Math.min(count - 1, Math.floor(((x - padL) / (W - padL - padR)) * count));
    const body = onIndex(i);
    if (!body) return leave();
    tip.replaceChildren(...body);
    tip.hidden = false;
    const left = ((ev.clientX - r.left) / r.width) > 0.55 ? (ev.clientX - r.left) - 176 : (ev.clientX - r.left) + 14;
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = "8px";
  };
  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerdown", move);
  svg.addEventListener("pointerleave", leave);
}
// bars: [{ v, label, cls: "pos"|"neg", tip: [title, value] }]
function barChart(bars, { W, height = 200, tick = (v) => num(v), aria = "", labelEvery = 1 }) {
  const wrap = h("div", { class: "chart" });
  const H = height, narrow = W < 500, padL = narrow ? 40 : 54, padR = 8, padT = 10, padB = 24;
  const vals = bars.map((b) => b.v);
  const sc = niceScale(Math.min(0, ...vals), Math.max(0, ...vals));
  if (sc.hi - sc.lo < 1e-9) Object.assign(sc, { lo: 0, hi: 1, step: 1 });
  const Y = (v) => padT + (1 - (v - sc.lo) / (sc.hi - sc.lo)) * (H - padT - padB);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img", "aria-label": aria });
  for (let v = sc.lo; v <= sc.hi + sc.step / 2; v += sc.step) {
    const y = Y(v);
    svg.append(s("line", { x1: padL, x2: W - padR, y1: y, y2: y, stroke: Math.abs(v) < sc.step / 2 ? "var(--axis)" : "var(--grid)",
      "stroke-width": 1, "vector-effect": "non-scaling-stroke" }));
    const t = s("text", { x: padL - 8, y: y + 4, "text-anchor": "end", fill: "var(--muted)", "font-size": 11 });
    t.textContent = tick(Math.abs(v) < sc.step / 2 ? 0 : v);
    svg.append(t);
  }
  const bw = (W - padL - padR) / bars.length, gap = Math.min(3, bw * 0.18);
  const rects = bars.map((b, i) => {
    const y = Math.min(Y(b.v), Y(0)), hgt = Math.max(b.v ? 1.5 : 0, Math.abs(Y(b.v) - Y(0)));
    const r = s("rect", { x: padL + i * bw + gap, y, width: Math.max(1, bw - 2 * gap), height: hgt, rx: Math.min(2.5, bw / 4),
      fill: b.cls === "neg" ? "var(--bad)" : "var(--good)", opacity: 0.88 });
    svg.append(r);
    if (b.label && i % labelEvery === 0) {
      const last = i === bars.length - 1 && bw < 30; // keeps the last label inside the card
      const t = s("text", { x: last ? W - padR : padL + i * bw + bw / 2, y: H - 6, "text-anchor": last ? "end" : "middle",
        fill: "var(--muted)", "font-size": 11 });
      t.textContent = b.label;
      svg.append(t);
    }
    return r;
  });
  svg.style.height = `${H}px`;
  wrap.append(svg);
  hoverTip(wrap, svg, W, (i) => {
    rects.forEach((r, k) => r.setAttribute("opacity", i < 0 || k === i ? 0.88 : 0.35));
    if (i < 0 || !bars[i].tip) return null;
    return [h("div", { class: "t" }, bars[i].tip[0]), h("div", { class: "row" }, h("span", {}, bars[i].tip[1]), h("strong", {}, bars[i].tip[2]))];
  }, bars.length, padL, padR);
  return wrap;
}
const dayKey = (ms) => { const d = new Date(ms); return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; };
function dailyPnl(trades, book, days = 30) {
  const sums = new Map(), counts = new Map();
  for (const t of trades) {
    if (t.book !== book) continue;
    const k = dayKey(t.exit_ts);
    sums.set(k, (sums.get(k) || 0) + t.pnl); counts.set(k, (counts.get(k) || 0) + 1);
  }
  const out = [], today = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i), k = dayKey(d.getTime());
    const v = sums.get(k) || 0, n = counts.get(k) || 0;
    const name = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    out.push({ v, label: name, cls: v < 0 ? "neg" : "pos", tip: [name, `${n} trade${n === 1 ? "" : "s"}`, signedText(v)] });
  }
  return out;
}
function dailyCard(d, b, col) {
  const bars = dailyPnl(d.trades, b.name);
  const up = bars.filter((x) => x.v > 0).length, down = bars.filter((x) => x.v < 0).length;
  const W = chartWidth(col), every = W < 500 ? 10 : 5;
  bars.forEach((x, i) => { if ((bars.length - 1 - i) % every) x.label = ""; });
  const chart = barChart(bars, { W, height: 210, tick: (v) => (v ? (v > 0 ? "+" : "−") + compact(Math.abs(v)) : "0"),
    aria: `Daily realized P&L, ${label(b.name)}` });
  return h("section", { class: "card" }, head("Daily P&L", `${label(b.name)} · last 30 days`,
    h("div", { class: "legend" }, h("span", {}, h("span", { class: "swatch", style: "background:var(--good)" }), `${up} green days`),
      h("span", {}, h("span", { class: "swatch", style: "background:var(--bad)" }), `${down} red`))), chart);
}
function returnsCard(d, b, col) {
  const rs = d.trades.filter((t) => t.book === b.name).map((t) => t.return_pct);
  if (rs.length < 3) return card("Trade returns", label(b.name), emptyState("Not enough trades yet", null));
  const sc = niceScale(Math.min(...rs), Math.max(...rs), 12);
  const n = Math.max(1, Math.round((sc.hi - sc.lo) / sc.step));
  const bins = Array.from({ length: n }, (_, i) => ({ a: sc.lo + i * sc.step, c: 0 }));
  for (const r of rs) bins[Math.min(n - 1, Math.max(0, Math.floor((r - sc.lo) / sc.step)))].c++;
  const fmtP = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${num(Math.abs(v), 2)}%`;
  const W = chartWidth(col), every = Math.max(1, Math.ceil(n / (W < 400 ? 4 : 6)));
  const bars = bins.map((x, i) => ({ v: x.c, cls: x.a + sc.step / 2 < 0 ? "neg" : "pos",
    label: i % every ? "" : fmtP(x.a), tip: [`${fmtP(x.a)} to ${fmtP(x.a + sc.step)}`, "trades", String(x.c)] }));
  const avg = rs.reduce((a, x) => a + x, 0) / rs.length;
  const chart = barChart(bars, { W, height: 210, tick: (v) => num(v, 0), aria: `Distribution of trade returns, ${label(b.name)}` });
  return h("section", { class: "card" }, head("Trade returns", `${rs.length} trades · avg ${fmtP(avg)} per trade`), chart);
}
function drawdownChart(books, { W, height = 190, focus = null }) {
  const wrap = h("div", { class: "chart" });
  const series = books.filter((b) => b.curve && b.curve.length > 1).map((b) => {
    let peak = -Infinity;
    return { b, pts: b.curve.map(([t, v]) => { peak = Math.max(peak, v); return [t, peak > 0 ? (v / peak - 1) * 100 : 0]; }) };
  });
  if (!series.length) { wrap.append(emptyState("No equity curve yet", null)); return wrap; }
  const H = height, narrow = W < 500, padL = narrow ? 44 : 64, padR = 12, padT = 10, padB = 26;
  const all = series.flatMap((x) => x.pts);
  const x0 = Math.min(...all.map((p) => p[0])), x1 = Math.max(...all.map((p) => p[0])) || x0 + 1;
  const sc = niceScale(Math.min(-0.5, ...all.map((p) => p[1])), 0, 4);
  const X = (t) => padL + ((t - x0) / Math.max(1, x1 - x0)) * (W - padL - padR);
  const Y = (v) => padT + (1 - (v - sc.lo) / (sc.hi - sc.lo)) * (H - padT - padB);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img", "aria-label": "Drawdown from peak per wallet" });
  for (let v = sc.lo; v <= 1e-9; v += sc.step) {
    svg.append(s("line", { x1: padL, x2: W - padR, y1: Y(v), y2: Y(v), stroke: Math.abs(v) < 1e-9 ? "var(--axis)" : "var(--grid)",
      "stroke-width": 1, "vector-effect": "non-scaling-stroke" }));
    const t = s("text", { x: padL - 10, y: Y(v) + 4, "text-anchor": "end", fill: "var(--muted)", "font-size": 11 });
    t.textContent = Math.abs(v) < 1e-9 ? "0%" : `−${num(Math.abs(v), 1)}%`;
    svg.append(t);
  }
  for (const frac of [0, 0.5, 1]) {
    const t = s("text", { x: padL + frac * (W - padL - padR), y: H - 6, fill: "var(--muted)", "font-size": 11,
      "text-anchor": frac === 0 ? "start" : frac === 1 ? "end" : "middle" });
    t.textContent = new Date(x0 + frac * (x1 - x0)).toLocaleDateString("en-US", { month: "short", day: "numeric" });
    svg.append(t);
  }
  const defs = s("defs", {});
  svg.append(defs);
  const ordered = focus ? [...series.filter((x) => x.b.name !== focus), ...series.filter((x) => x.b.name === focus)] : series;
  for (const { b, pts } of ordered) {
    const color = COLORS[b.name] || "var(--muted)", isFocus = !focus || b.name === focus;
    const d = pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
    if (isFocus && (focus || series.length === 1)) {
      const id = `dd-${b.name}`;
      const grad = s("linearGradient", { id, x1: 0, y1: 0, x2: 0, y2: 1 });
      grad.append(s("stop", { offset: "0%", "stop-color": "var(--bad)", "stop-opacity": 0.05 }),
        s("stop", { offset: "100%", "stop-color": "var(--bad)", "stop-opacity": 0.35 }));
      defs.append(grad);
      svg.append(s("path", { d: `${d}L${X(pts[pts.length - 1][0]).toFixed(1)},${Y(0)}L${X(pts[0][0]).toFixed(1)},${Y(0)}Z`, fill: `url(#${id})` }));
    }
    svg.append(s("path", { d, fill: "none", stroke: color, "stroke-width": isFocus ? 1.8 : 1.3, opacity: isFocus ? 1 : 0.5,
      "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke" }));
  }
  svg.style.height = `${H}px`;
  wrap.append(svg);
  const N = 120;
  hoverTip(wrap, svg, W, (i) => {
    if (i < 0) return null;
    const t = x0 + ((i + 0.5) / N) * (x1 - x0);
    const near = (pts) => pts.reduce((a, p) => (Math.abs(p[0] - t) < Math.abs(a[0] - t) ? p : a), pts[0]);
    return [h("div", { class: "t" }, dt(t)), ...series.map((x) => h("div", { class: "row" },
      h("span", {}, swatch(x.b.name), label(x.b.name)), h("strong", {}, pct(near(x.pts)[1]))))];
  }, N, padL, padR);
  wrap.legend = h("div", { class: "legend" }, series.map((x) => h("span", {}, swatch(x.b.name), label(x.b.name))));
  return wrap;
}

// ---------------------------------------------------------------- blocos reutilizados
const REASONS = { stop: "stop", alvo: "target", tempo: "time" };
const reasonText = (r) => REASONS[r] || (String(r).startsWith("decider") ? "decision" : r);
const byExit = (a, b) => (b.exit_ts || 0) - (a.exit_ts || 0);
function tradeItem(t) {
  const win = t.pnl > 0, fmt = money(t.book);
  return item({
    ico: icon(win ? "up" : "down", win ? "ok" : "bad"),
    title: [h("span", { class: "sym" }, t.label || t.symbol), t.side === "short" ? h("span", { class: "badge warn" }, "short") : null,
      h("span", { class: `badge ${win ? "ok" : "bad"}` }, win ? "win" : "loss")],
    sub: [who(t.book), sep(), reasonText(t.exit_reason), sep(), dt(t.exit_ts)],
    end: signed(t.pnl, fmt), endSub: signed(t.return_pct, (v) => pct(v)),
  });
}
function positionItem(p) {
  const fmt = money(p.book);
  return item({
    ico: icon("clock", tone(p.pnl) === "pos" ? "ok" : tone(p.pnl) === "neg" ? "bad" : ""),
    title: [h("span", { class: "sym" }, p.label || p.symbol), p.side === "short" ? h("span", { class: "badge warn" }, "short") : null,
      h("span", { class: "badge" }, "open")],
    sub: [who(p.book), sep(), `${px(p.entry_price)} → ${px(p.price)}`],
    end: signed(p.pnl, fmt), endSub: signed(p.pnl_pct, (v) => pct(v)),
    note: p.target ? `Value ${fmt(p.value)} · stop ${px(p.stop)} · target ${px(p.target)} · since ${dt(p.entry_ts)}`
      + (MEME.has(p.book) ? " · price = what selling now would get (impact + fee)" : "")
      : `Value ${fmt(p.value)} · RISK exit at 20:05 NY · since ${dt(p.entry_ts)}`,
  });
}
function pickBook(d) {
  const names = d.books.map((b) => b.name);
  if (!names.includes(st.book)) st.book = names.includes("jev") ? "jev" : names[0];
  return d.books.find((b) => b.name === st.book);
}
function bookSeg(d) {
  return seg(d.books.map((b) => [b.name, label(b.name), b.name]), st.book, (v) => { st.book = v; render(); });
}
function statusOf(sy) {
  if (!sy.running) return ["bad", "Stopped", false];
  if (sy.kill) return ["bad", "Kill switch", false];
  if (sy.paused) return ["warn", "Paused", false];
  if (sy.circuit_open) return ["warn", "Circuit open", false];
  return ["ok", "Running", true];
}
function setStatus(cls, text, live) {
  const pill = document.getElementById("status-pill");
  pill.replaceChildren(h("i", { class: `dot ${cls}${live ? " live" : ""}` }), h("span", {}, text));
}

// ---------------------------------------------------------------- Visão
function scoreboard(b, trades) {
  const p = b.performance, n = p.trades;
  const wins = Math.round((n * (p.win_rate || 0)) / 100), losses = n - wins;
  const mine = trades.filter((t) => t.book === b.name).sort(byExit);
  const best = mine.length ? Math.max(...mine.map((t) => t.pnl)) : null;
  const worst = mine.length ? Math.min(...mine.map((t) => t.pnl)) : null;
  let streak = "—";
  if (mine.length) {
    const w = mine[0].pnl > 0;
    let k = 0;
    while (k < mine.length && (mine[k].pnl > 0) === w) k++;
    streak = `${k} ${w ? (k > 1 ? "wins" : "win") : (k > 1 ? "losses" : "loss")}`;
  }
  return card("Scoreboard", `${label(b.name)} · ${n} closed trades`,
    h("div", { class: "score" },
      h("div", { class: "score-nums" },
        h("div", { class: "score-num win" }, h("div", { class: "k" }, "Wins"), h("div", { class: "v" }, String(wins))),
        h("div", { class: "score-num loss" }, h("div", { class: "k" }, "Losses"), h("div", { class: "v" }, String(losses)))),
      n ? h("div", { class: "bar", role: "img", "aria-label": `${wins} wins and ${losses} losses` },
        wins ? h("i", { class: "w", style: `width:${(wins / n) * 100}%` }) : null,
        losses ? h("i", { class: "l", style: `width:${(losses / n) * 100}%` }) : null) : h("div", { class: "bar" }),
      h("dl", { class: "facts" },
        h("dt", {}, "Win rate"), h("dd", {}, pct(p.win_rate, 1)),
        h("dt", {}, "Profit factor"), h("dd", {}, num(p.profit_factor)),
        h("dt", {}, "Best trade"), h("dd", {}, signed(best)),
        h("dt", {}, "Worst trade"), h("dd", {}, signed(worst)),
        h("dt", {}, "Current streak"), h("dd", {}, streak),
        h("dt", {}, "Avg win / loss"), h("dd", {}, `${brl(p.avg_win)} / ${brl(p.avg_loss)}`))));
}
function renderOverview(d) {
  if (!d.books.length) {
    return [card("No wallets yet", d.mode, emptyState("The bot has not run in this environment yet",
      "On your computer: python -m rafs run --dashboard --lan"))];
  }
  const b = pickBook(d), sy = d.system;
  const change = b.equity - b.initial;
  const tile = (k, v, sub) => h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v), sub ? h("div", { class: "s" }, sub) : null);
  const strip = deskStrip(d);
  const hero = h("section", { class: "hero" },
    h("div", { class: "hero-top" }, bookSeg(d),
      h("span", { class: "eyebrow" }, `${d.exchange} · ${d.timeframe} · heartbeat ${ago(sy.heartbeat)}`)),
    h("div", {},
      h("div", { class: "eyebrow" }, swatch(b.name), `Simulated equity · ${label(b.name)}`),
      h("div", { class: "hero-value" }, h("span", { class: "cur" }, cur().trim()), nf2.format(b.equity)),
      h("div", { class: "delta-row" },
        h("span", { class: `delta ${tone(change)}` }, `${signedText(change)}  ${signedText(b.return_pct, (v) => pct(v))}`),
        h("span", {}, `since start · capital ${brl(b.initial)}`))),
    h("div", { class: "grid tiles" },
      tile("Realized", signed(b.realized), `today ${signedText(b.realized_today)}`),
      tile("Unrealized", signed(b.unrealized), `${b.positions.length} ${b.positions.length === 1 ? "position" : "positions"}`),
      tile("Cash", brl(b.cash), `deployed ${brl(b.exposure)}`),
      tile("Drawdown", pct(b.drawdown_pct), `max ${pct(b.max_drawdown_pct)}`)));
  const chart = equityChart(d.books, { focus: b.name });
  const trades = [...d.trades].sort(byExit);
  const recent = trades.filter((t) => t.book === b.name).slice(0, 8);
  const positions = d.books.flatMap((x) => x.positions);
  const ranking = [...d.books].sort((x, y) => y.equity - x.equity);
  return [
    strip,
    hero,
    h("div", { class: "split" },
      h("section", { class: "card" }, head("Equity", "dashed line = starting capital", chart.legend || null), chart),
      scoreboard(b, d.trades)),
    h("div", { class: "split" }, dailyCard(d, b, "main"), returnsCard(d, b, "side")),
    h("div", { class: "split" },
      flush("Recent trades", `${label(b.name)} · wins and losses`, null,
        list(recent.map(tradeItem), emptyState("No closed trades yet", "Every entry closes by stop, target, time or decision.")),
        recent.length ? h("button", { class: "more", onclick: () => { st.perfBook = b.name; goTab("perf"); } }, "Full history →") : null),
      h("div", { class: "grid" },
        flush("Open positions", `${positions.length} total`, null,
          list(positions.map(positionItem), emptyState("No open positions", null))),
        flush("Wallets", "tap to focus", null, list(ranking.map((x, i) => item({
          ico: h("span", { class: "ico mono" }, String(i + 1)),
          title: [swatch(x.name), label(x.name)],
          sub: `${x.performance.trades} trades · win rate ${pct(x.performance.win_rate, 0)}`,
          end: brl(x.equity), endSub: signed(x.return_pct, (v) => pct(v)),
          onclick: () => { st.book = x.name; window.scrollTo(0, 0); render(); },
        })), null)))),
  ];
}

// ---------------------------------------------------------------- Ao vivo
function renderLive(d) {
  const pairs = d.market.map((m) => {
    const rsi = m.rsi == null ? null : Math.max(0, Math.min(100, m.rsi));
    return h("section", { class: "card pair" },
      h("div", { class: "pair-top" }, h("span", { class: "mono", style: "font-weight:600" }, m.symbol),
        h("span", { class: "badge" }, m.regime || "—")),
      h("div", { class: "price" }, num(m.price)),
      h("dl", { class: "facts" },
        h("dt", {}, "Bid / Ask"), h("dd", {}, `${num(m.bid)} / ${num(m.ask)}`),
        h("dt", {}, "Spread"), h("dd", {}, pct(m.spread_pct, 3)),
        h("dt", {}, "Momentum"), h("dd", {}, signed(m.momentum_pct, (v) => pct(v))),
        h("dt", {}, "Relative volume"), h("dd", {}, m.rel_volume == null ? "—" : `${num(m.rel_volume)}×`),
        h("dt", {}, "RSI"), h("dd", {}, num(m.rsi, 0))),
      h("div", { class: "meter", title: "RSI (30 and 70 bands)" }, rsi == null ? null : h("i", { style: `left:calc(${rsi}% - 1px)` })),
      h("div", { class: "li-sub", style: "margin-top:12px" }, `quote ${ago(m.quote_ts)}`));
  });
  const have = new Set([...d.books, ...(d.memes?.books || [])].map((b) => b.name));
  const deciders = ["jev", "baseline", "random", "memes", "memes_top", "all"].filter((x) => x === "all" || have.has(x));
  if (!deciders.includes(st.decider)) st.decider = deciders[0];
  const decs = d.decisions.filter((x) => st.decider === "all" || x.decider === st.decider).slice(0, 40);
  const act = { BUY: ["up", "ok", "BUY"], SELL: ["down", "bad", "SELL"], HOLD: ["hold", "", "HOLD"] };
  const signals = decs.map((r) => {
    const [ic, cls, txt] = act[r.action] || act.HOLD;
    return item({
      ico: icon(ic, cls),
      title: [h("span", { class: "sym" }, r.label || r.symbol), who(r.decider)],
      sub: [dt(r.candle_ts), r.confidence == null ? null : sep(), r.confidence == null ? null : `confidence ${num(r.confidence * 100, 0)}%`,
        r.credits ? sep() : null, r.credits ? `${num(r.credits, 0)} cr.` : null],
      end: h("span", { class: `badge ${cls}` }, txt), note: r.reason,
    });
  });
  const orders = d.orders.map((r) => {
    const ok = r.status === "filled";
    return item({
      ico: icon(ok ? "check" : "x", ok ? "ok" : "bad"),
      title: [r.side === "buy" ? "Buy" : "Sell", h("span", { class: "sym" }, r.label || r.symbol)],
      sub: [who(r.book), sep(), dt(r.ts)],
      end: `${MEME.has(r.book) ? compact(r.amount) : num(r.amount, 6)} @ ${px(r.price)}`,
      endSub: ok ? `fee ${money(r.book)(r.fee)}` : "rejected",
      note: r.note || null,
    });
  });
  const trades = [...d.trades].sort(byExit).slice(0, 30);
  return [
    h("div", { class: "grid market" }, pairs.length ? pairs : [card("Market", null, emptyState("No quotes yet", "Is the bot running?"))]),
    h("div", { class: "split" },
      flush("Signals", "a decision on every closed candle",
        seg(deciders.map((x) => [x, x === "all" ? "All" : label(x), x === "all" ? null : x]), st.decider, (v) => { st.decider = v; render(); }),
        list(signals, emptyState("No decisions yet", null))),
      h("div", { class: "grid" },
        flush("Orders", "cleared SIZE and RISK", null, list(orders, emptyState("No orders yet", null))),
        flush("Closed trades", "last 30", null, list(trades.map(tradeItem), emptyState("No closed trades yet", null))))),
  ];
}

// ---------------------------------------------------------------- Agentes
function renderAgents(d) {
  const chip = { ok: ["ok", "active", true], error: ["bad", "error"], stale: ["warn", "stale"], stopped: ["", "stopped"],
    idle: ["", "idle"], off: ["", "off"] };
  const cards = d.agents.map((a) => {
    const [cls, txt, live] = chip[a.status] || ["", a.status];
    return h("section", { class: "card agent" },
      h("div", { class: "agent-top" }, h("span", { class: "agent-name" }, a.name),
        h("span", { class: `badge ${cls}` }, h("i", { class: `dot ${cls}${live ? " live" : ""}` }), txt)),
      h("div", { class: "agent-desc" }, a.description),
      h("div", { class: "metrics" },
        h("div", {}, h("div", { class: "k" }, "Last run"), h("div", { class: "v" }, ago(a.ts))),
        h("div", {}, h("div", { class: "k" }, "Latency"), h("div", { class: "v" }, a.latency_ms == null ? "—" : `${num(a.latency_ms, 0)} ms`)),
        h("div", {}, h("div", { class: "k" }, "Runs"), h("div", { class: "v" }, a.runs == null ? "—" : num(a.runs, 0)))),
      a.activity ? h("div", { class: "activity" }, a.activity)
        : h("div", { class: "agent-desc", style: "margin:0" }, a.status === "off" ? (a.off_reason || "Off.") : "Has not run yet."),
      a.last_error ? h("div", { class: "agent-err" }, `Last error (${ago(a.last_error_ts)}): ${a.last_error}`) : null,
      a.memes ? h("div", { class: `activity meme${a.memes.ok === false ? " err" : ""}` },
        h("span", { class: "tag" }, `Memes · ${ago(a.memes.ts)}`), a.memes.activity) : null);
  });
  const out = [];
  const tile = (k, v, sub) => h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v), sub ? h("div", { class: "s" }, sub) : null);
  if (d.desk) out.push(...deskCards(d.desk, tile));
  if (d.grok) {
    const g = d.grok;
    out.push(card("Grok · CHIEF", g.enabled ? `${g.model} · reads market and trades, escalates only what matters to Jev`
      : "off (no XAI_API_KEY): Jev is asked directly", h("div", { class: "grid tiles" },
      tile("Reads today", num(g.calls_today, 0),
        `${num(g.calls_total, 0)} total · ${g.max_calls_per_day ? `cap ${g.max_calls_per_day}/day` : "no cap"}`),
      tile("Escalated to Jev", num(g.escalated_total, 0), `${pct((g.escalated_total / Math.max(1, g.calls_total)) * 100, 0)} of reads`),
      tile("Held by Grok", num(d.jev?.grok_screened || 0, 0), "no Jev call spent"),
      tile("xAI cost", `$${num(g.usd_total, 4)}`, "as reported by the API"))));
  }
  if (d.jev) {
    const j = d.jev, c = j.counts || {};
    const rule = j.prompt_version === "v2"
      ? `prompt v2 · buys when the odds of target before stop clear break-even (costs ${pct(j.cost_pct)}) + ${num(j.edge_margin * 100, 0)} pts`
      : `prompt v1 · buys only at probability ≥ ${pct(j.min_probability * 100, 0)}`;
    out.push(card("Jev", rule, h("div", { class: "grid tiles" },
      tile("Credits spent", num(j.credits_spent, 0)),
      tile("Calls today", String(j.calls_today),
        `${j.calls_total} total · ${j.max_calls_per_day ? `cap ${j.max_calls_per_day}/day` : "no cap"}`),
      tile("Calls saved", String(j.skipped || 0), j.trend_gate === false ? "filter off"
        : `${pct(((j.skipped || 0) / Math.max(1, (j.skipped || 0) + j.calls_total)) * 100, 0)} · trend filter`),
      tile("Buy / sell / hold", `${c.BUY || 0} / ${c.SELL || 0} / ${c.HOLD || 0}`))));
  }
  out.push(h("div", { class: "grid agents" }, cards));
  return out;
}

// ---------------------------------------------------------------- Mesa (guia Hedge Fund Terminal)
const GREENS = {
  liquidity: ["Liquidity (4 weeks)", (i) => `${num(i.net_liq_4w, 1)} bn`],
  volatility: ["VIX below 22", (i) => num(i.vix, 2)],
  credit: ["HY credit not worse", (i) => `${num(i.hy_4w, 3)} pts`],
  btc: ["BTC above 200d average", (i) => pct(i.btc_vs_200d == null ? null : i.btc_vs_200d * 100)],
  sol: ["SOL above 50d average", (i) => pct(i.sol_vs_50d == null ? null : i.sol_vs_50d * 100)],
  nasdaq: ["Nasdaq-100 above 200d", (i) => pct(i.qqq_vs_200d == null ? null : i.qqq_vs_200d * 100)],
};
function inText(ms) {
  const min = Math.max(0, Math.round((ms - Date.now()) / 60000));
  return min < 60 ? `in ${min} min` : `in ${Math.floor(min / 60)} h ${String(min % 60).padStart(2, "0")}`;
}
const hhmm = (ms) => new Date(ms).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
function deskState(k) {
  const dial = k.dial_valid ? k.dial : null;
  if (k.flat.length) return ["bad", `RISK: ${k.flat.map(label).join(", ")} FLAT today (account down 4% on the day). Back tomorrow.`];
  if (!dial) return ["warn", "No QUANT dial published: NO DIAL, NO DESK. No entries until the dial is out."];
  const c = dial.dial.crypto;
  const targets = Object.entries(dial.crypto_targets || {}).map(([b, w]) => `${b} ${num(w, 2)}`).join(" + ");
  if (c < 0.5) return ["warn", `Crypto dial ${num(c, 2)} below 0.50: desk idle today. Open positions keep their stop and target.`];
  if (!targets) return ["warn", `Crypto dial ${num(c, 2)}, but no asset with positive 12-1 momentum: cash.`];
  return ["ok", `Crypto dial ${num(c, 2)} · targets ${targets} · size × ${num(Math.min(1, c), 2)}`];
}
function deskStrip(d) {
  const k = d.desk;
  if (!k) return null;
  const [cls, text] = deskState(k);
  return h("button", { class: `desk-strip ${cls === "ok" ? "" : cls}`, onclick: () => goTab("agents") },
    h("i", { class: `dot ${cls}` }), h("span", { class: "grow" }, text),
    h("span", { class: "go" }, k.rules ? "desk →" : "desk (quant book only) →"));
}
function deskCards(k, tile) {
  const out = [];
  const dial = k.dial;
  const [cls, text] = deskState(k);
  const nx = k.next || {};
  const sub = `${k.rules ? "desk rules apply to every book" : "desk rules on the quant book only"} · `
    + `day ${k.today} (NY) · next dial ${hhmm(nx.dial)} your time, ${inText(nx.dial)}`;
  if (!dial) {
    out.push(card("Desk · QUANT", sub, h("p", { class: "muted", style: "margin:0" }, text)));
  } else {
    const inp = dial.inputs || {};
    const greens = Object.entries(dial.greens || {}).map(([g, ok]) => {
      const [name, val] = GREENS[g] || [g, () => "—"];
      return h("li", {}, h("span", { class: `badge ${ok ? "ok" : "bad"}` }, ok ? "green" : "red"),
        h("span", { class: "what" }, name, h("div", { class: "d" }, val(inp))), h("span", { class: "ms" }, ""));
    });
    const targets = Object.entries(dial.crypto_targets || {});
    const mom = Object.entries(dial.momentum_12_1 || {}).map(([b, v]) => `${b} ${v == null ? "n/a" : pct(v * 100, 1)}`).join(" · ");
    out.push(card("Desk · QUANT", sub,
      h("div", { class: `desk-strip ${cls === "ok" ? "" : cls}`, style: "cursor:default" }, h("i", { class: `dot ${cls}` }),
        h("span", { class: "grow" }, text)),
      h("div", { class: "grid tiles" },
        tile("Crypto dial", num(dial.dial.crypto, 2), dial.dial.crypto < 0.5 ? "below 0.50: idle" : "trading"),
        tile("12-1 targets", targets.length ? targets.map(([b]) => b).join(" + ") : "cash", mom),
        tile("Meme dial", num(dial.dial.meme, 2), !k.memes_on ? "sleeve off (MEME_SLEEVE=0)"
          : dial.dial.meme < 0.5 ? "below 0.50: scan only" : "memes in PAPER · Memes tab"),
        tile("Stocks dial", num(dial.dial.stocks, 2), "sleeve off (no Alpaca)")),
      h("ul", { class: "checks" }, greens),
      h("p", { class: "muted", style: "margin:0;font-size:12.5px" },
        `Published ${ago(dial.computed_at)} for ${dial.date}. ` + (dial.degraded?.length
          ? `DEGRADED (counts as red): ${dial.degraded.join(", ")}.` : "All series on time."))));
  }
  const proof = k.proof;
  if (proof) {
    const checks = Object.entries(proof.checks || {}).map(([name, c]) => h("li", {},
      h("span", { class: `badge ${c.ok ? "ok" : "bad"}` }, c.ok ? "ok" : "failed"),
      h("span", { class: "what" }, name, h("div", { class: "d" }, c.detail)), h("span", { class: "ms" }, `${num(c.ms, 0)} ms`)));
    out.push(card("CHIEF · tool check", `${proof.day} · ${proof.ready ? "READY" : `DEGRADED (${(proof.degraded || []).map(label).join(", ") || "no book affected"})`}`
      + ` · ${k.blocked_today} candles blocked by the desk today · ${k.fills_today} fills`,
      h("ul", { class: "checks" }, checks),
      h("p", { class: "muted", style: "margin:0;font-size:12.5px" },
        `Off: ${Object.values(k.off || {}).join(" · ")}. Crypto session ${hhmm(nx.crypto)} ${inText(nx.crypto)}, report ${hhmm(nx.report)} ${inText(nx.report)}.`)));
  }
  if (k.report) {
    out.push(card("Desk report", `sent at 23:55 NY · ${k.report_day || ""}`,
      h("details", { class: "more-info" }, h("summary", {}, "Show report"), h("pre", { class: "report" }, k.report))));
  }
  return out;
}

// ---------------------------------------------------------------- Memes (SCAN → CHIEF → JUDGE → SIZE → FILLS → RISK)
function memeState(m, k) {
  const dial = k && k.dial_valid ? k.dial : null;
  if (!m.enabled) return ["warn", "Meme sleeve off (MEME_SLEEVE=0). Only history shows here."];
  if (m.flat.length) return ["bad", `RISK: ${m.flat.map(label).join(", ")} FLAT today (bank down 4% on the day). Back tomorrow.`];
  if (!m.running) return ["bad", `Meme desk stopped (last heartbeat ${ago(m.heartbeat)}).`];
  if (!dial) return ["warn", "No QUANT dial: scans and logs, nothing goes to JUDGE (NO DIAL, NO DESK)."];
  const v = dial.dial.meme;
  if (v < 0.5) return ["warn", `Meme dial ${num(v, 2)} below 0.50: scans and logs, nothing goes to JUDGE.`];
  return ["ok", `Meme dial ${num(v, 2)} · ticket × ${num(v, 2)} · SCAN every 15 min, RISK every 5 min`];
}
const CUT_TEXT = (cut) => Object.entries(cut || {}).sort((a, b) => b[1] - a[1]).map(([w, n]) => `${w} ${n}`).join(" · ");
function scanCard(ls, tile) {
  if (!ls) return card("Last SCAN", "GeckoTerminal", emptyState("No SCAN yet", "The first one runs right after the bot starts."));
  const cutN = Object.values(ls.cut || {}).reduce((a, b) => a + b, 0);
  const rows = (ls.candidates || []).map((c) => ({ ...c }));
  const t = table([
    { t: "#", num: 1, v: (r) => String(r.rank) },
    { t: "Token", v: (r) => h("span", { class: "nowrap" }, h("span", { class: "sym" }, r.label),
      r.walked_up ? h("span", { class: "badge warn", title: "price rose faster than holders" }, "pushed") : null) },
    { t: "Liquidity", num: 1, v: (r) => usd(r.liquidity).replace(".00", "") },
    { t: "Mcap", num: 1, v: (r) => compact(r.mcap) },
    { t: "Age", num: 1, v: (r) => `${num(r.age_h, 1)} h` },
    { t: "Price 1h", num: 1, v: (r) => signed(r.chg_h1, (v) => pct(v, 1)) },
    { t: "Holders/h", num: 1, v: (r) => r.holders_growth_h1 == null ? "—" : signed(r.holders_growth_h1, (v) => pct(v, 2)) },
    { t: "Top 10", num: 1, v: (r) => pct(r.top10_pct, 0) },
  ], rows, emptyState("No candidate passed", "Cut: 15 min to 72 h, liquidity > 12k, 24h volume > 40k, mcap 60k–8M."));
  return card("Last SCAN", `${dt(ls.ts)} · ${ago(ls.ts)} · ${ls.calls} GeckoTerminal calls`,
    h("div", { class: "grid tiles" },
      tile("Pools read", String(ls.universe), "trending 1h/6h + 24h volume"),
      tile("Cut out", String(cutN), CUT_TEXT(ls.cut) || "—"),
      tile("Sell-heavy / honeypot", `${ls.sells_dropped} / ${ls.honeypots}`, "sells > buys on 5m and 15m"),
      tile("Candidates", String((ls.candidates || []).length), "most holders/h first; pushed last")),
    ls.stand_down ? h("div", { class: "desk-strip warn", style: "cursor:default" }, h("i", { class: "dot warn" }),
      h("span", { class: "grow" }, `${ls.stand_down}: scanned and logged, nothing went to JUDGE.`)) : null,
    ls.errors?.length ? h("p", { class: "agent-err", style: "margin:0" }, ls.errors.join(" · ")) : null,
    t);
}
function chiefCard(ls, g) {
  const sub = `Grok · ${num(g.calls_today, 0)} read${g.calls_today === 1 ? "" : "s"} today${g.max_calls_per_day ? ` of ${g.max_calls_per_day}` : ""} · $${num(g.usd_total, 4)} total`;
  const r = ls?.grok;
  let body;
  if (!r) body = emptyState("Nothing to read in this SCAN", ls?.stand_down ? "Desk held by the dial." : "No candidates or book full.");
  else if (r.error || r.skipped) body = h("p", { class: r.error ? "agent-err" : "muted", style: "margin:0" }, r.error || r.skipped);
  else {
    const picks = (r.shortlist || []).map((lab) => h("li", {}, h("span", { class: "badge" }, lab),
      h("span", { class: "what" }, (r.names || {})[lab] || lab), h("span", { class: "ms" }, "")));
    body = [h("p", { style: "margin:0" }, r.summary),
      picks.length ? h("ul", { class: "checks" }, picks) : h("p", { class: "muted", style: "margin:0" }, "None is worth a Jev call."),
      r.risks?.length ? h("ul", { class: "risks" }, r.risks.map((x) => h("li", {}, x))) : null];
  }
  return card("CHIEF · Grok reads the SCAN", sub, h("p", { class: "fine", style: "margin:0" },
    "Gets only labels C1..Cn and numbers. Token name, symbol and links never reach Grok or Jev."), body);
}
function judgeCard(ls, j, cfg) {
  const sub = `Jev · ${j.calls_today} call${j.calls_today === 1 ? "" : "s"} today${j.max_calls_per_day ? ` of ${j.max_calls_per_day}` : ""} · ${num(j.credits, 0)} credits total`;
  const r = ls?.jev;
  let body;
  if (!r) body = emptyState("Jev was not called in this SCAN", "Grok shortlisted nobody or the desk was idle.");
  else if (r.skipped || (r.error && !r.pick)) body = h("p", { class: r.error ? "agent-err" : "muted", style: "margin:0" }, r.skipped || r.error);
  else {
    const probs = Object.entries(r.probabilities || {}).sort((a, b) => b[1] - a[1]);
    const rows = probs.map(([lab, p]) => h("li", {},
      h("span", { class: `badge ${lab === r.pick ? "ok" : ""}` }, lab),
      h("span", { class: "what" }, lab === "NONE" ? "none" : (r.names || {})[lab] || lab,
        h("div", { class: "bar", style: "margin-top:6px", role: "img", "aria-label": `${lab} ${num(p * 100, 0)}%` },
          h("i", { class: "p", style: `width:${Math.max(1, p * 100)}%` }))),
      h("span", { class: "ms" }, pct(p * 100, 0))));
    const picked = r.pick && r.pick !== "NONE";
    body = [h("p", { style: "margin:0" }, picked
      ? `Picked ${r.pick} ${(r.names || {})[r.pick] || ""}. Odds of +${num(cfg.tp, 0)}% before −${num(cfg.sl, 0)}%: ${r.p_target == null ? "—" : pct(r.p_target * 100, 0)}.`
      : `NONE: no candidate strong enough today.`),
      h("ul", { class: "checks" }, rows),
      picked && r.size ? h("div", { class: "activity" }, h("span", { class: "tag" }, `SIZE · ${r.ticket ? usd(r.ticket) : "no entry"}`), r.size) : null,
      r.fill ? h("p", { class: "muted", style: "margin:0" }, `FILLS: ${r.fill} (simulated)`) : null,
      r.error ? h("p", { class: "agent-err", style: "margin:0" }, r.error) : null];
  }
  return card("JUDGE · Jev picks one or none", sub, body);
}
function memeVerdict(m) {
  const a = m.books.find((b) => b.name === "memes"), b = m.books.find((x) => x.name === "memes_top");
  if (!a || !b) return null;
  const need = 30, n = Math.min(a.performance.trades, b.performance.trades);
  let text, cls, tag;
  if (n < need) { text = "Sample too small. Any difference so far may be luck."; cls = "warn"; tag = "waiting"; }
  else if (a.performance.net_pnl > b.performance.net_pnl && (a.performance.profit_factor ?? 0) > (b.performance.profit_factor ?? 0)) {
    text = "Grok + Jev pick better than SCAN #1 on net and profit factor (simulated)."; cls = "ok"; tag = "passed";
  } else { text = "Grok + Jev do not beat SCAN #1. The AI adds nothing: stop or change the hypothesis."; cls = "bad"; tag = "stop"; }
  return h("section", { class: "card" }, head("Meme verdict", "Grok + Jev vs SCAN #1, same exits", h("span", { class: `badge ${cls}` }, tag)),
    h("div", { class: "verdict" },
      h("div", {}, h("div", { class: "progress-label" }, h("span", {}, "Closed trades (the smaller of the two)"), h("span", { class: "mono" }, `${n}/${need}`)),
        h("div", { class: "bar", style: "margin-top:8px", role: "progressbar", "aria-valuenow": n, "aria-valuemax": need },
          h("i", { class: "p", style: `width:${Math.min(100, (n / need) * 100)}%` }))),
      h("p", {}, text)));
}
function renderMemes(d) {
  const m = d.memes;
  if (!m) return [card("Memes", null, emptyState("Meme sleeve off", "MEME_SLEEVE=0 in .env"))];
  const tile = (k, v, sub) => h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v), sub ? h("div", { class: "s" }, sub) : null);
  const [cls, text] = memeState(m, d.desk);
  const ls = m.last_scan, cfg = m.config;
  const strip = h("div", { class: `desk-strip ${cls === "ok" ? "" : cls}`, style: "cursor:default" }, h("i", { class: `dot ${cls}` }),
    h("span", { class: "grow" }, text), h("span", { class: "go" }, ls ? `SCAN ${ago(ls.ts)}` : "no SCAN"));
  const books = m.books.map((b) => tile([swatch(b.name), " ", label(b.name)], usd(b.equity),
    `${signedText(b.return_pct, (v) => pct(v))} · ${b.positions.length} open · ${b.performance.trades} trades`));
  const chart = equityChart(m.books, { height: 220, fmt: usd, wide: true });
  const positions = m.books.flatMap((b) => b.positions);
  const trades = m.trades.slice(0, 30);
  const control = ls?.control;
  return [
    strip,
    h("section", { class: "card" }, head("Simulated banks", `$${num(cfg.bank, 0)} each · ${cfg.nets.join(" + ")} · simulated`, chart.legend || null),
      h("div", { class: "grid tiles" }, books.length ? books : [tile("Memes", "—", "shows up after the first SCAN")]), chart),
    scanCard(ls, tile),
    h("div", { class: "split" }, chiefCard(ls, m.grok), judgeCard(ls, m.jev, cfg)),
    control ? card("Control · SCAN #1", "no Grok, no Jev, fixed 6% ticket × dial, same exits", h("p", { style: "margin:0" }, control)) : null,
    h("div", { class: "split" },
      flush("Open positions", `max ${cfg.max_positions} per book · RISK checks every 5 min`, null,
        list(positions.map(positionItem), emptyState("No open positions", null))),
      flush("Closed trades", `target +${num(cfg.tp, 0)}% · stop −${num(cfg.sl, 0)}% · ${cfg.hold_h} h · 6h volume < 20% of avg · liquidity < 25%`, null,
        list(trades.map(tradeItem), emptyState("No closed trades yet", null)))),
    memeVerdict(m),
  ].filter(Boolean);
}

// ---------------------------------------------------------------- Desempenho
function verdict(d) {
  const jev = d.books.find((b) => b.name === "jev");
  if (!jev) return card("Verdict", null, h("p", { class: "muted", style: "margin:0" }, "Jev off (no JEV_API_KEY). Comparing baseline vs random only."));
  const n = jev.performance.trades, need = 100;
  const others = d.books.filter((b) => b.name !== "jev");
  const beats = others.every((o) => jev.performance.net_pnl > o.performance.net_pnl &&
    (jev.performance.profit_factor ?? 0) > (o.performance.profit_factor ?? 0));
  let text, cls, tag;
  if (n < need) { text = `Sample too small. Any result so far may be luck.`; cls = "warn"; tag = "waiting"; }
  else if ((jev.performance.expectancy ?? 0) <= 0) { text = "Stop rule hit: net expectancy ≤ 0. Stop or change the hypothesis."; cls = "bad"; tag = "stop"; }
  else if (!beats) { text = "Stop rule hit: Jev does not beat baseline and random. Stop or change the hypothesis."; cls = "bad"; tag = "stop"; }
  else { text = "Jev beats baseline and random on net P&L and profit factor (simulated)."; cls = "ok"; tag = "passed"; }
  return h("section", { class: "card" }, head("Verdict", "rule set before any results", h("span", { class: `badge ${cls}` }, tag)),
    h("div", { class: "verdict" },
      h("div", {}, h("div", { class: "progress-label" }, h("span", {}, "Jev closed trades"), h("span", { class: "mono" }, `${n}/${need}`)),
        h("div", { class: "bar", style: "margin-top:8px", role: "progressbar", "aria-valuenow": n, "aria-valuemax": need },
          h("i", { class: "p", style: `width:${Math.min(100, (n / need) * 100)}%` }))),
      h("p", {}, text)));
}
function renderPerf(d) {
  if (!d.books.length) return [card("No data", null, emptyState("No wallets yet", null))];
  const rows = d.books.map((b) => ({ name: b.name, ...b.performance, dd: b.max_drawdown_pct }));
  const comp = table([
    { t: "Wallet", v: (r) => who(r.name) },
    { t: "Trades", num: 1, v: (r) => String(r.trades) },
    { t: "Net", num: 1, v: (r) => signed(r.net_pnl) },
    { t: "Return", num: 1, v: (r) => signed(r.return_pct, (v) => pct(v)) },
    { t: "Win rate", num: 1, v: (r) => pct(r.win_rate, 1) },
    { t: "Profit factor", num: 1, v: (r) => num(r.profit_factor) },
    { t: "Expectancy", num: 1, v: (r) => signed(r.expectancy) },
    { t: "Avg win", num: 1, v: (r) => brl(r.avg_win) },
    { t: "Avg loss", num: 1, v: (r) => brl(r.avg_loss) },
    { t: "Fees", num: 1, v: (r) => brl(r.fees) },
    { t: "Max DD", num: 1, v: (r) => pct(r.dd) },
  ], rows);
  const books = [["all", "All", null], ...d.books.map((b) => [b.name, label(b.name), b.name])];
  if (!books.some(([v]) => v === st.perfBook)) st.perfBook = "all";
  const trades = [...d.trades].sort(byExit).filter((t) => st.perfBook === "all" || t.book === st.perfBook);
  const shown = trades.slice(0, st.histLimit);
  const wins = trades.filter((t) => t.pnl > 0).length;
  const focus = st.perfBook === "all" ? null : st.perfBook;
  const chart = equityChart(d.books, { height: 300, focus, wide: true });
  const dd = drawdownChart(d.books, { W: chartWidth("full"), focus: focus || (d.books.some((b) => b.name === "jev") ? "jev" : null) });
  const sel = d.books.find((b) => b.name === (focus || "jev")) || d.books[0];
  return [
    verdict(d),
    card("Comparison", `fee ${pct(d.limits.fee_rate * 100, 2)}/side · slippage ${num(d.limits.slippage_bps, 0)} bps · net values`, comp),
    h("section", { class: "card" }, head("Equity curve", null, chart.legend || null), chart),
    h("section", { class: "card" }, head("Drawdown", "distance from each wallet's running peak", dd.legend || null), dd),
    h("div", { class: "split" }, dailyCard(d, sel, "main"), returnsCard(d, sel, "side")),
    flush("Full history", `${trades.length} trades · ${wins} wins · ${trades.length - wins} losses`,
      seg(books, st.perfBook, (v) => { st.perfBook = v; st.histLimit = 60; render(); }),
      list(shown.map(tradeItem), emptyState("No closed trades yet", null)),
      trades.length > shown.length ? h("button", { class: "more", onclick: () => { st.histLimit += 100; render(); } },
        `Show more (${trades.length - shown.length})`) : null),
  ];
}

// ---------------------------------------------------------------- Controles
async function control(action, opts) {
  const confirm = await ask(opts);
  if (confirm === null) return;
  try {
    const r = await api("/api/control", { method: "POST", body: JSON.stringify({ env: st.env, action, confirm }) });
    toast(r.message);
    load();
  } catch (e) { toast(`Error: ${e.message}`); }
}
function renderControls(d) {
  const sy = d.system, L = d.limits;
  const setting = (title, text, btn) => h("div", { class: "setting" }, h("div", {}, h("h3", {}, title), h("p", {}, text)), btn);
  const lvl = { ERROR: ["alert", "bad"], RISK: ["alert", "warn"], CONTROL: ["bolt", "ok"], RECONCILE: ["info", "warn"] };
  const events = d.events.slice(0, 60).map((r) => {
    const [ic, cls] = lvl[r.level] || ["info", ""];
    return item({ ico: icon(ic, cls), title: h("span", { class: `badge ${cls}` }, r.level), end: h("span", { class: "li-sub" }, dt(r.ts)), note: r.message });
  });
  return [
    h("section", { class: "card" }, head("Operation", `environment ${d.mode}`),
      setting("New entries", sy.paused ? "Paused. Stop, target and time exits keep working."
        : "Allowed. The bot opens and closes positions on its own.",
      sy.paused
        ? h("button", { class: "btn primary", onclick: () => control("resume", { title: "Resume entries?", text: "The bot goes back to opening positions on its own.", word: "RESUME", okLabel: "Resume" }) }, "Resume")
        : h("button", { class: "btn", onclick: () => control("pause", { title: "Pause new entries?", text: "Open positions keep their stop and target.", okLabel: "Pause" }) }, "Pause")),
      h("p", { class: "fine" }, "The dashboard can only brake. No withdrawals, keys, limit changes or real money from here.")),
    h("section", { class: "card danger-zone" }, head("Kill switch", sy.kill ? "armed" : "off"),
      setting(sy.kill ? "Kill switch armed" : "Emergency stop",
        sy.kill ? "No new entries in any environment until you disarm it." : "Blocks new entries in every environment at once.",
        sy.kill
          ? h("button", { class: "btn", onclick: () => control("kill_off", { title: "Disarm the kill switch?", text: "New entries are allowed again.", word: "DISARM", okLabel: "Disarm" }) }, "Disarm")
          : h("button", { class: "btn danger", onclick: () => control("kill_on", { title: "Arm the kill switch?", text: "Blocks new entries in every environment.", okLabel: "Arm", danger: true }) }, "Arm kill switch"))),
    h("div", { class: "split" },
      card("Risk limits", "read-only · set in .env", h("dl", { class: "facts" },
        h("dt", {}, "Capital per wallet"), h("dd", {}, brl(L.capital)),
        h("dt", {}, "Max per entry"), h("dd", {}, brl(L.entry)),
        h("dt", {}, "Max exposure"), h("dd", {}, brl(L.max_exposure)),
        h("dt", {}, "Max daily loss"), h("dd", {}, brl(L.daily_loss)),
        h("dt", {}, "Positions per pair"), h("dd", {}, String(L.max_positions_per_symbol)),
        h("dt", {}, "Stop / target"), h("dd", {}, `${num(L.stop_atr)}× / ${num(L.target_atr)}× ATR`),
        h("dt", {}, "Time exit"), h("dd", {}, `${L.max_hold_candles} candles`),
        h("dt", {}, "Fee / slippage"), h("dd", {}, `${pct(L.fee_rate * 100)} / ${num(L.slippage_bps, 0)} bps`))),
      card("Environment", "the selector up top only changes what the dashboard shows", h("dl", { class: "facts" },
        h("dt", {}, "Showing"), h("dd", {}, d.mode),
        h("dt", {}, "Exchange"), h("dd", {}, d.exchange),
        h("dt", {}, "Pairs"), h("dd", { class: "mono" }, d.symbols.join(", ")),
        h("dt", {}, "Timeframe"), h("dd", {}, d.timeframe),
        h("dt", {}, "Heartbeat"), h("dd", {}, ago(sy.heartbeat))))),
    flush("Logs", "latest events", null, list(events, emptyState("No events", null))),
    h("div", { style: "display:flex;justify-content:center" }, h("button", { class: "btn ghost", onclick: async () => {
      try { await api("/api/logout", { method: "POST", body: "{}" }); } catch (e) { /* ignora */ }
      st.csrf = null; showLogin();
    } }, "Sign out")),
  ];
}

// ---------------------------------------------------------------- render
function render() {
  const d = st.data;
  if (!d) return;
  document.getElementById("disclaimer").textContent = `${d.mode} · ${d.disclaimer}`;
  const [cls, text, live] = statusOf(d.system);
  setStatus(cls, text, live);
  const sel = document.getElementById("env");
  sel.replaceChildren(...d.modes.map((m) => h("option", { value: m, selected: m === d.mode }, m)));
  const views = { overview: renderOverview, live: renderLive, memes: renderMemes, agents: renderAgents, perf: renderPerf, controls: renderControls };
  const main = document.getElementById("main");
  const y = window.scrollY;
  main.classList.toggle("enter", st.animate);
  st.animate = false;
  main.replaceChildren(...views[st.tab](d));
  window.scrollTo(0, y);
}
function goTab(tab) {
  st.tab = tab;
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x.dataset.tab === tab));
  st.animate = true;
  window.scrollTo({ top: 0, behavior: "instant" });
  render();
}

document.getElementById("tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (b) goTab(b.dataset.tab);
});
let resizeTimer = null;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => st.data && render(), 200); });
document.getElementById("env").addEventListener("change", (e) => {
  st.env = e.target.value;
  localStorage.setItem("gb_env", st.env);
  st.animate = true;
  load();
});
document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = document.getElementById("login-error");
  err.textContent = "";
  try {
    const r = await api("/api/login", { method: "POST", body: JSON.stringify({ password: document.getElementById("pw").value }) });
    st.csrf = r.csrf;
    document.getElementById("pw").value = "";
    showApp();
  } catch (ex) { err.textContent = ex.message === "HTTP 401" ? "Wrong password." : ex.message; }
});
document.addEventListener("visibilitychange", () => { if (!document.hidden && st.csrf) load(); });

// ---------------------------------------------------------------- background: halftone character
// rafs-art.png is a small tone map (gray = intensity); the dots are drawn here, sharp on any screen.
const art = { img: null, tone: null, w: 0, h: 0, timer: null };
function paintArt() {
  const cv = document.getElementById("art");
  if (!art.tone) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const vw = window.innerWidth, vh = window.innerHeight;
  cv.width = Math.round(vw * dpr);
  cv.height = Math.round(vh * dpr);
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, vw, vh);
  // o personagem ocupa a altura da tela, encostado à direita (no celular, centralizado e menor)
  const narrow = vw < 700;
  const h = narrow ? vh * 0.7 : vh * 1.02;
  const w = h * (art.w / art.h);
  const x0 = narrow ? (vw - w) / 2 + w * 0.12 : vw - w * 0.96;
  const y0 = narrow ? vh * 0.18 : -vh * 0.02;
  const step = vw < 700 ? 5 : 6;
  for (let y = Math.max(0, y0); y < Math.min(vh, y0 + h); y += step) {
    const ty = Math.min(art.h - 1, Math.floor(((y - y0) / h) * art.h));
    for (let x = Math.max(0, x0); x < Math.min(vw, x0 + w); x += step) {
      const tx = Math.min(art.w - 1, Math.floor(((x - x0) / w) * art.w));
      const v = art.tone[ty * art.w + tx] / 255;
      if (v < 0.07) continue;
      const r = step * 0.44 * Math.pow(v, 0.55);
      const c = Math.round(110 + 120 * v);
      ctx.fillStyle = `rgba(${c},${c},${c + 10},${(0.35 + 0.65 * v).toFixed(3)})`;
      ctx.beginPath();
      ctx.arc(x + step / 2, y + step / 2, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  cv.classList.add("ready");
}
(function loadArt() {
  const img = new Image();
  img.onload = () => {
    const c = document.createElement("canvas");
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    const ctx = c.getContext("2d");
    ctx.drawImage(img, 0, 0);
    const px = ctx.getImageData(0, 0, c.width, c.height).data;
    art.tone = new Uint8ClampedArray(c.width * c.height);
    for (let i = 0; i < art.tone.length; i++) art.tone[i] = px[i * 4];
    art.w = c.width; art.h = c.height;
    paintArt();
  };
  img.src = "/static/rafs-art.png";
  window.addEventListener("resize", () => { clearTimeout(art.timer); art.timer = setTimeout(paintArt, 150); });
})();

(async () => {
  try {
    const r = await api("/api/session");
    if (r.authenticated) { st.csrf = r.csrf; showApp(); } else showLogin();
  } catch (e) { showLogin(); }
})();
