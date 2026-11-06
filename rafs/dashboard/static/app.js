"use strict";
// Painel rafs. Todo dado entra no DOM via textContent (nunca innerHTML).

const COLORS = { jev: "var(--s-jev)", baseline: "var(--s-baseline)", random: "var(--s-random)", quant: "var(--s-quant)",
  memes: "var(--s-memes)", memes_top: "var(--s-memes-top)" };
const LABELS = { jev: "Jev", baseline: "Baseline", random: "Aleatório", quant: "Quant 12-1",
  memes: "Memes Grok+Jev", memes_top: "Memes topo SCAN" };
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
const nf2 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
// moeda de cotação do modo exibido (BRL no PAPER; USDT se a testnet usar pares em USDT)
const cur = () => (st.data && st.data.quote && st.data.quote !== "BRL" ? st.data.quote + " " : "R$ ");
const brl = (v) => (v == null ? "—" : cur() + nf2.format(v));
const usd = (v) => (v == null ? "—" : "US$ " + nf2.format(v)); // banca simulada dos memes
const money = (book) => (MEME.has(book) ? usd : brl);
const num = (v, d = 2) => (v == null ? "—" : new Intl.NumberFormat("pt-BR", { maximumFractionDigits: d }).format(v));
// preço de memecoin tem muitos zeros: abaixo de 1, 4 dígitos significativos
const px = (v) => (v == null ? "—" : Math.abs(v) >= 1 ? num(v) : new Intl.NumberFormat("pt-BR", { maximumSignificantDigits: 4 }).format(v));
const compact = (v) => (v == null ? "—" : new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 }).format(v));
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
const dt = (ms) => (ms ? new Date(ms).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
function ago(ms) {
  if (!ms) return "nunca";
  const sec = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (sec < 60) return `há ${sec}s`;
  if (sec < 3600) return `há ${Math.round(sec / 60)} min`;
  if (sec < 86400) return `há ${Math.round(sec / 3600)} h`;
  return `há ${Math.round(sec / 86400)} d`;
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
  if (!rows.length) return empty || emptyState("Nada ainda", null);
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
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("sessão expirada"); }
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
    if (e.message !== "sessão expirada") setStatus("bad", "Sem conexão", false);
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
  ok.textContent = okLabel || "Confirmar";
  ok.className = `btn ${danger ? "danger" : "primary"}`;
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => {
      if (dlg.returnValue !== "ok") return resolve(null);
      const typed = input.value.trim().toUpperCase();
      if (word && typed !== word) { toast("Cancelado: a palavra não confere."); return resolve(null); }
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
    wrap.append(emptyState("Curva ainda vazia", "Aparece depois dos primeiros candles processados."));
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
    "aria-label": "Curva de patrimônio simulado por carteira" });
  for (let i = 0; i <= ticks; i++) {
    const v = y0 + step * i, y = Y(v);
    svg.append(s("line", { x1: padL, x2: W - padR, y1: y, y2: y, stroke: "var(--grid)", "stroke-width": 1, "vector-effect": "non-scaling-stroke" }));
    const t = s("text", { x: padL - 10, y: y + 4, "text-anchor": "end", fill: "var(--muted)", "font-size": 11 });
    t.textContent = step >= 1 ? Math.round(v).toLocaleString("pt-BR") : nf2.format(v);
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

// ---------------------------------------------------------------- blocos reutilizados
const REASONS = { stop: "stop", alvo: "alvo", tempo: "tempo" };
const reasonText = (r) => REASONS[r] || (String(r).startsWith("decider") ? "decisão" : r);
const byExit = (a, b) => (b.exit_ts || 0) - (a.exit_ts || 0);
function tradeItem(t) {
  const win = t.pnl > 0, fmt = money(t.book);
  return item({
    ico: icon(win ? "up" : "down", win ? "ok" : "bad"),
    title: [h("span", { class: "sym" }, t.label || t.symbol), t.side === "short" ? h("span", { class: "badge warn" }, "short") : null,
      h("span", { class: `badge ${win ? "ok" : "bad"}` }, win ? "vitória" : "derrota")],
    sub: [who(t.book), sep(), reasonText(t.exit_reason), sep(), dt(t.exit_ts)],
    end: signed(t.pnl, fmt), endSub: signed(t.return_pct, (v) => pct(v)),
  });
}
function positionItem(p) {
  const fmt = money(p.book);
  return item({
    ico: icon("clock", tone(p.pnl) === "pos" ? "ok" : tone(p.pnl) === "neg" ? "bad" : ""),
    title: [h("span", { class: "sym" }, p.label || p.symbol), p.side === "short" ? h("span", { class: "badge warn" }, "short") : null,
      h("span", { class: "badge" }, "aberta")],
    sub: [who(p.book), sep(), `${px(p.entry_price)} → ${px(p.price)}`],
    end: signed(p.pnl, fmt), endSub: signed(p.pnl_pct, (v) => pct(v)),
    note: p.target ? `Valor ${fmt(p.value)} · stop ${px(p.stop)} · alvo ${px(p.target)} · desde ${dt(p.entry_ts)}`
      + (MEME.has(p.book) ? " · preço = o que daria para vender agora (impacto + taxa)" : "")
      : `Valor ${fmt(p.value)} · saída pelo RISK das 20:05 NY · desde ${dt(p.entry_ts)}`,
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
  if (!sy.running) return ["bad", "Parado", false];
  if (sy.kill) return ["bad", "Kill switch", false];
  if (sy.paused) return ["warn", "Pausado", false];
  if (sy.circuit_open) return ["warn", "Circuit aberto", false];
  return ["ok", "Operando", true];
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
    streak = `${k} ${w ? (k > 1 ? "vitórias" : "vitória") : (k > 1 ? "derrotas" : "derrota")}`;
  }
  return card("Placar", `${label(b.name)} · ${n} trades fechados`,
    h("div", { class: "score" },
      h("div", { class: "score-nums" },
        h("div", { class: "score-num win" }, h("div", { class: "k" }, "Vitórias"), h("div", { class: "v" }, String(wins))),
        h("div", { class: "score-num loss" }, h("div", { class: "k" }, "Derrotas"), h("div", { class: "v" }, String(losses)))),
      n ? h("div", { class: "bar", role: "img", "aria-label": `${wins} vitórias e ${losses} derrotas` },
        wins ? h("i", { class: "w", style: `width:${(wins / n) * 100}%` }) : null,
        losses ? h("i", { class: "l", style: `width:${(losses / n) * 100}%` }) : null) : h("div", { class: "bar" }),
      h("dl", { class: "facts" },
        h("dt", {}, "Taxa de acerto"), h("dd", {}, pct(p.win_rate, 1)),
        h("dt", {}, "Profit factor"), h("dd", {}, num(p.profit_factor)),
        h("dt", {}, "Melhor trade"), h("dd", {}, signed(best)),
        h("dt", {}, "Pior trade"), h("dd", {}, signed(worst)),
        h("dt", {}, "Sequência atual"), h("dd", {}, streak),
        h("dt", {}, "Ganho / perda média"), h("dd", {}, `${brl(p.avg_win)} / ${brl(p.avg_loss)}`))));
}
function renderOverview(d) {
  if (!d.books.length) {
    return [card("Nenhuma carteira ainda", d.mode, emptyState("O bot ainda não rodou neste ambiente",
      "No computador: python -m rafs run --dashboard --lan"))];
  }
  const b = pickBook(d), sy = d.system;
  const change = b.equity - b.initial;
  const tile = (k, v, sub) => h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v), sub ? h("div", { class: "s" }, sub) : null);
  const strip = deskStrip(d);
  const hero = h("section", { class: "hero" },
    h("div", { class: "hero-top" }, bookSeg(d),
      h("span", { class: "eyebrow" }, `${d.exchange} · ${d.timeframe} · sinal de vida ${ago(sy.heartbeat)}`)),
    h("div", {},
      h("div", { class: "eyebrow" }, swatch(b.name), `Patrimônio simulado · ${label(b.name)}`),
      h("div", { class: "hero-value" }, h("span", { class: "cur" }, cur().trim()), nf2.format(b.equity)),
      h("div", { class: "delta-row" },
        h("span", { class: `delta ${tone(change)}` }, `${signedText(change)}  ${signedText(b.return_pct, (v) => pct(v))}`),
        h("span", {}, `desde o início · capital ${brl(b.initial)}`))),
    h("div", { class: "grid tiles" },
      tile("Realizado", signed(b.realized), `hoje ${signedText(b.realized_today)}`),
      tile("Em aberto", signed(b.unrealized), `${b.positions.length} ${b.positions.length === 1 ? "posição" : "posições"}`),
      tile("Caixa", brl(b.cash), `aplicado ${brl(b.exposure)}`),
      tile("Drawdown", pct(b.drawdown_pct), `máx. ${pct(b.max_drawdown_pct)}`)));
  const chart = equityChart(d.books, { focus: b.name });
  const trades = [...d.trades].sort(byExit);
  const recent = trades.filter((t) => t.book === b.name).slice(0, 8);
  const positions = d.books.flatMap((x) => x.positions);
  const ranking = [...d.books].sort((x, y) => y.equity - x.equity);
  return [
    strip,
    hero,
    h("div", { class: "split" },
      h("section", { class: "card" }, head("Patrimônio", "linha tracejada = capital inicial", chart.legend || null), chart),
      scoreboard(b, d.trades)),
    h("div", { class: "split" },
      flush("Transações recentes", `${label(b.name)} · vitórias e derrotas`, null,
        list(recent.map(tradeItem), emptyState("Nenhum trade fechado ainda", "Cada compra fecha por stop, alvo, tempo ou decisão.")),
        recent.length ? h("button", { class: "more", onclick: () => { st.perfBook = b.name; goTab("perf"); } }, "Ver histórico completo →") : null),
      h("div", { class: "grid" },
        flush("Posições abertas", `${positions.length} no total`, null,
          list(positions.map(positionItem), emptyState("Nenhuma posição aberta", null))),
        flush("Carteiras", "toque para focar", null, list(ranking.map((x, i) => item({
          ico: h("span", { class: "ico mono" }, String(i + 1)),
          title: [swatch(x.name), label(x.name)],
          sub: `${x.performance.trades} trades · acerto ${pct(x.performance.win_rate, 0)}`,
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
        h("dt", {}, "Volume relativo"), h("dd", {}, m.rel_volume == null ? "—" : `${num(m.rel_volume)}×`),
        h("dt", {}, "RSI"), h("dd", {}, num(m.rsi, 0))),
      h("div", { class: "meter", title: "RSI (faixas 30 e 70)" }, rsi == null ? null : h("i", { style: `left:calc(${rsi}% - 1px)` })),
      h("div", { class: "li-sub", style: "margin-top:12px" }, `cotação ${ago(m.quote_ts)}`));
  });
  const have = new Set([...d.books, ...(d.memes?.books || [])].map((b) => b.name));
  const deciders = ["jev", "baseline", "random", "memes", "memes_top", "all"].filter((x) => x === "all" || have.has(x));
  if (!deciders.includes(st.decider)) st.decider = deciders[0];
  const decs = d.decisions.filter((x) => st.decider === "all" || x.decider === st.decider).slice(0, 40);
  const act = { BUY: ["up", "ok", "COMPRA"], SELL: ["down", "bad", "VENDA"], HOLD: ["hold", "", "SEGURA"] };
  const signals = decs.map((r) => {
    const [ic, cls, txt] = act[r.action] || act.HOLD;
    return item({
      ico: icon(ic, cls),
      title: [h("span", { class: "sym" }, r.label || r.symbol), who(r.decider)],
      sub: [dt(r.candle_ts), r.confidence == null ? null : sep(), r.confidence == null ? null : `confiança ${num(r.confidence * 100, 0)}%`,
        r.credits ? sep() : null, r.credits ? `${num(r.credits, 0)} créd.` : null],
      end: h("span", { class: `badge ${cls}` }, txt), note: r.reason,
    });
  });
  const orders = d.orders.map((r) => {
    const ok = r.status === "filled";
    return item({
      ico: icon(ok ? "check" : "x", ok ? "ok" : "bad"),
      title: [r.side === "buy" ? "Compra" : "Venda", h("span", { class: "sym" }, r.label || r.symbol)],
      sub: [who(r.book), sep(), dt(r.ts)],
      end: `${MEME.has(r.book) ? compact(r.amount) : num(r.amount, 6)} @ ${px(r.price)}`,
      endSub: ok ? `taxa ${money(r.book)(r.fee)}` : "rejeitada",
      note: r.note || null,
    });
  });
  const trades = [...d.trades].sort(byExit).slice(0, 30);
  return [
    h("div", { class: "grid market" }, pairs.length ? pairs : [card("Mercado", null, emptyState("Sem cotações ainda", "O bot está rodando?"))]),
    h("div", { class: "split" },
      flush("Sinais", "decisões a cada candle fechado",
        seg(deciders.map((x) => [x, x === "all" ? "Todos" : label(x), x === "all" ? null : x]), st.decider, (v) => { st.decider = v; render(); }),
        list(signals, emptyState("Nenhuma decisão ainda", null))),
      h("div", { class: "grid" },
        flush("Ordens", "passaram por SIZE e RISK", null, list(orders, emptyState("Nenhuma ordem ainda", null))),
        flush("Trades fechados", "últimos 30", null, list(trades.map(tradeItem), emptyState("Nenhum trade fechado ainda", null))))),
  ];
}

// ---------------------------------------------------------------- Agentes
function renderAgents(d) {
  const chip = { ok: ["ok", "ativo", true], error: ["bad", "erro"], stale: ["warn", "atrasado"], stopped: ["", "parado"],
    idle: ["", "sem atividade"], off: ["", "desligado"] };
  const cards = d.agents.map((a) => {
    const [cls, txt, live] = chip[a.status] || ["", a.status];
    return h("section", { class: "card agent" },
      h("div", { class: "agent-top" }, h("span", { class: "agent-name" }, a.name),
        h("span", { class: `badge ${cls}` }, h("i", { class: `dot ${cls}${live ? " live" : ""}` }), txt)),
      h("div", { class: "agent-desc" }, a.description),
      h("div", { class: "metrics" },
        h("div", {}, h("div", { class: "k" }, "Última"), h("div", { class: "v" }, ago(a.ts))),
        h("div", {}, h("div", { class: "k" }, "Latência"), h("div", { class: "v" }, a.latency_ms == null ? "—" : `${num(a.latency_ms, 0)} ms`)),
        h("div", {}, h("div", { class: "k" }, "Execuções"), h("div", { class: "v" }, a.runs == null ? "—" : num(a.runs, 0)))),
      a.activity ? h("div", { class: "activity" }, a.activity)
        : h("div", { class: "agent-desc", style: "margin:0" }, a.status === "off" ? (a.off_reason || "Desligado.") : "Ainda não rodou."),
      a.last_error ? h("div", { class: "agent-err" }, `Último erro (${ago(a.last_error_ts)}): ${a.last_error}`) : null,
      a.memes ? h("div", { class: `activity meme${a.memes.ok === false ? " err" : ""}` },
        h("span", { class: "tag" }, `Memes · ${ago(a.memes.ts)}`), a.memes.activity) : null);
  });
  const out = [];
  const tile = (k, v, sub) => h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v), sub ? h("div", { class: "s" }, sub) : null);
  if (d.desk) out.push(...deskCards(d.desk, tile));
  if (d.grok) {
    const g = d.grok;
    out.push(card("Grok · CHIEF", g.enabled ? `${g.model} · lê mercado e operações, só escala ao Jev o que importa`
      : "desligado (sem XAI_API_KEY): o Jev é consultado direto", h("div", { class: "grid tiles" },
      tile("Leituras hoje", num(g.calls_today, 0),
        `${num(g.calls_total, 0)} no total · ${g.max_calls_per_day ? `limite ${g.max_calls_per_day}/dia` : "sem limite"}`),
      tile("Escaladas ao Jev", num(g.escalated_total, 0), `${pct((g.escalated_total / Math.max(1, g.calls_total)) * 100, 0)} das leituras`),
      tile("Seguradas pelo Grok", num(d.jev?.grok_screened || 0, 0), "sem gastar chamada do Jev"),
      tile("Custo xAI", `US$ ${num(g.usd_total, 4)}`, "custo informado pela API"))));
  }
  if (d.jev) {
    const j = d.jev, c = j.counts || {};
    const rule = j.prompt_version === "v2"
      ? `prompt v2 · compra se a chance de bater o alvo antes do stop passar do equilíbrio (custos ${pct(j.cost_pct)}) + ${num(j.edge_margin * 100, 0)} pts`
      : `prompt v1 · compra só com probabilidade ≥ ${pct(j.min_probability * 100, 0)}`;
    out.push(card("Jev", rule, h("div", { class: "grid tiles" },
      tile("Créditos gastos", num(j.credits_spent, 0)),
      tile("Chamadas hoje", String(j.calls_today),
        `${j.calls_total} no total · ${j.max_calls_per_day ? `limite ${j.max_calls_per_day}/dia` : "sem limite"}`),
      tile("Chamadas poupadas", String(j.skipped || 0), j.trend_gate === false ? "filtro desligado"
        : `${pct(((j.skipped || 0) / Math.max(1, (j.skipped || 0) + j.calls_total)) * 100, 0)} · filtro de tendência`),
      tile("Compra / venda / segura", `${c.BUY || 0} / ${c.SELL || 0} / ${c.HOLD || 0}`))));
  }
  out.push(h("div", { class: "grid agents" }, cards));
  return out;
}

// ---------------------------------------------------------------- Mesa (guia Hedge Fund Terminal)
const GREENS = {
  liquidity: ["Liquidez (4 semanas)", (i) => `${num(i.net_liq_4w, 1)} bi`],
  volatility: ["VIX abaixo de 22", (i) => num(i.vix, 2)],
  credit: ["Crédito HY não piorou", (i) => `${num(i.hy_4w, 3)} pts`],
  btc: ["BTC acima da média 200d", (i) => pct(i.btc_vs_200d == null ? null : i.btc_vs_200d * 100)],
  sol: ["SOL acima da média 50d", (i) => pct(i.sol_vs_50d == null ? null : i.sol_vs_50d * 100)],
  nasdaq: ["Nasdaq-100 acima da 200d", (i) => pct(i.qqq_vs_200d == null ? null : i.qqq_vs_200d * 100)],
};
function inText(ms) {
  const min = Math.max(0, Math.round((ms - Date.now()) / 60000));
  return min < 60 ? `em ${min} min` : `em ${Math.floor(min / 60)} h ${String(min % 60).padStart(2, "0")}`;
}
const hhmm = (ms) => new Date(ms).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
function deskState(k) {
  const dial = k.dial_valid ? k.dial : null;
  if (k.flat.length) return ["bad", `RISK: ${k.flat.map(label).join(", ")} FLAT hoje (conta caiu 4% no dia). Só amanhã volta a entrar.`];
  if (!dial) return ["warn", "Sem dial do QUANT publicado: NO DIAL, NO DESK. Nenhuma entrada até o dial sair."];
  const c = dial.dial.crypto;
  const targets = Object.entries(dial.crypto_targets || {}).map(([b, w]) => `${b} ${num(w, 2)}`).join(" + ");
  if (c < 0.5) return ["warn", `Dial crypto ${num(c, 2)} abaixo de 0,50: mesa parada hoje. Posições abertas seguem com stop e alvo.`];
  if (!targets) return ["warn", `Dial crypto ${num(c, 2)}, mas nenhum ativo com momentum 12-1 positivo: caixa.`];
  return ["ok", `Dial crypto ${num(c, 2)} · alvos ${targets} · tamanho × ${num(Math.min(1, c), 2)}`];
}
function deskStrip(d) {
  const k = d.desk;
  if (!k) return null;
  const [cls, text] = deskState(k);
  return h("button", { class: `desk-strip ${cls === "ok" ? "" : cls}`, onclick: () => goTab("agents") },
    h("i", { class: `dot ${cls}` }), h("span", { class: "grow" }, text),
    h("span", { class: "go" }, k.rules ? "mesa →" : "mesa (só book quant) →"));
}
function deskCards(k, tile) {
  const out = [];
  const dial = k.dial;
  const [cls, text] = deskState(k);
  const nx = k.next || {};
  const sub = `${k.rules ? "regras da mesa valem para todos os books" : "regras da mesa só no book quant"} · `
    + `dia ${k.today} (NY) · próximo dial ${hhmm(nx.dial)} no seu horário, ${inText(nx.dial)}`;
  if (!dial) {
    out.push(card("Mesa · QUANT", sub, h("p", { class: "muted", style: "margin:0" }, text)));
  } else {
    const inp = dial.inputs || {};
    const greens = Object.entries(dial.greens || {}).map(([g, ok]) => {
      const [name, val] = GREENS[g] || [g, () => "—"];
      return h("li", {}, h("span", { class: `badge ${ok ? "ok" : "bad"}` }, ok ? "verde" : "vermelho"),
        h("span", { class: "what" }, name, h("div", { class: "d" }, val(inp))), h("span", { class: "ms" }, ""));
    });
    const targets = Object.entries(dial.crypto_targets || {});
    const mom = Object.entries(dial.momentum_12_1 || {}).map(([b, v]) => `${b} ${v == null ? "n/a" : pct(v * 100, 1)}`).join(" · ");
    out.push(card("Mesa · QUANT", sub,
      h("div", { class: `desk-strip ${cls === "ok" ? "" : cls}`, style: "cursor:default" }, h("i", { class: `dot ${cls}` }),
        h("span", { class: "grow" }, text)),
      h("div", { class: "grid tiles" },
        tile("Dial crypto", num(dial.dial.crypto, 2), dial.dial.crypto < 0.5 ? "abaixo de 0,50: parado" : "operando"),
        tile("Alvos 12-1", targets.length ? targets.map(([b]) => b).join(" + ") : "caixa", mom),
        tile("Dial meme", num(dial.dial.meme, 2), !k.memes_on ? "sleeve desligado (MEME_SLEEVE=0)"
          : dial.dial.meme < 0.5 ? "abaixo de 0,50: só escaneia" : "memes em PAPER · aba Memes"),
        tile("Dial stocks", num(dial.dial.stocks, 2), "sleeve desligado (sem Alpaca)")),
      h("ul", { class: "checks" }, greens),
      h("p", { class: "muted", style: "margin:0;font-size:12.5px" },
        `Publicado ${ago(dial.computed_at)} para ${dial.date}. ` + (dial.degraded?.length
          ? `DEGRADED (conta como vermelho): ${dial.degraded.join(", ")}.` : "Todas as séries no prazo."))));
  }
  const proof = k.proof;
  if (proof) {
    const checks = Object.entries(proof.checks || {}).map(([name, c]) => h("li", {},
      h("span", { class: `badge ${c.ok ? "ok" : "bad"}` }, c.ok ? "ok" : "falhou"),
      h("span", { class: "what" }, name, h("div", { class: "d" }, c.detail)), h("span", { class: "ms" }, `${num(c.ms, 0)} ms`)));
    out.push(card("CHIEF · prova das ferramentas", `${proof.day} · ${proof.ready ? "READY" : `DEGRADED (${(proof.degraded || []).map(label).join(", ") || "nenhum book afetado"})`}`
      + ` · ${k.blocked_today} candles barrados pela mesa hoje · ${k.fills_today} fills`,
      h("ul", { class: "checks" }, checks),
      h("p", { class: "muted", style: "margin:0;font-size:12.5px" },
        `Desligado: ${Object.values(k.off || {}).join(" · ")}. Sessão crypto ${hhmm(nx.crypto)} ${inText(nx.crypto)}, relatório ${hhmm(nx.report)} ${inText(nx.report)}.`)));
  }
  if (k.report) {
    out.push(card("Relatório da mesa", `enviado às 23:55 NY · ${k.report_day || ""}`,
      h("details", { class: "more-info" }, h("summary", {}, "Ver relatório"), h("pre", { class: "report" }, k.report))));
  }
  return out;
}

// ---------------------------------------------------------------- Memes (SCAN → CHIEF → JUDGE → SIZE → FILLS → RISK)
function memeState(m, k) {
  const dial = k && k.dial_valid ? k.dial : null;
  if (!m.enabled) return ["warn", "Sleeve de memes desligado (MEME_SLEEVE=0). Só o histórico aparece aqui."];
  if (m.flat.length) return ["bad", `RISK: ${m.flat.map(label).join(", ")} FLAT hoje (banca caiu 4% no dia). Volta amanhã.`];
  if (!m.running) return ["bad", `Mesa dos memes parada (último sinal ${ago(m.heartbeat)}).`];
  if (!dial) return ["warn", "Sem dial do QUANT: escaneia e registra, nada vai ao JUDGE (NO DIAL, NO DESK)."];
  const v = dial.dial.meme;
  if (v < 0.5) return ["warn", `Dial meme ${num(v, 2)} abaixo de 0,50: escaneia e registra, nada vai ao JUDGE.`];
  return ["ok", `Dial meme ${num(v, 2)} · ticket × ${num(v, 2)} · SCAN a cada 15 min, RISK a cada 5 min`];
}
const CUT_TEXT = (cut) => Object.entries(cut || {}).sort((a, b) => b[1] - a[1]).map(([w, n]) => `${w} ${n}`).join(" · ");
function scanCard(ls, tile) {
  if (!ls) return card("Último SCAN", "GeckoTerminal", emptyState("Nenhum SCAN ainda", "O primeiro roda logo depois que o bot sobe."));
  const cutN = Object.values(ls.cut || {}).reduce((a, b) => a + b, 0);
  const rows = (ls.candidates || []).map((c) => ({ ...c }));
  const t = table([
    { t: "#", num: 1, v: (r) => String(r.rank) },
    { t: "Token", v: (r) => h("span", { class: "nowrap" }, h("span", { class: "sym" }, r.label),
      r.walked_up ? h("span", { class: "badge warn", title: "preço subiu mais rápido que os holders" }, "empurrado") : null) },
    { t: "Liquidez", num: 1, v: (r) => usd(r.liquidity).replace(",00", "") },
    { t: "Mcap", num: 1, v: (r) => compact(r.mcap) },
    { t: "Idade", num: 1, v: (r) => `${num(r.age_h, 1)} h` },
    { t: "Preço 1h", num: 1, v: (r) => signed(r.chg_h1, (v) => pct(v, 1)) },
    { t: "Holders/h", num: 1, v: (r) => r.holders_growth_h1 == null ? "—" : signed(r.holders_growth_h1, (v) => pct(v, 2)) },
    { t: "Top 10", num: 1, v: (r) => pct(r.top10_pct, 0) },
  ], rows, emptyState("Nenhum candidato passou", "Corte do guia: 15 min a 72 h, liquidez > 12k, volume 24h > 40k, mcap 60k–8M."));
  return card("Último SCAN", `${dt(ls.ts)} · ${ago(ls.ts)} · ${ls.calls} chamadas ao GeckoTerminal`,
    h("div", { class: "grid tiles" },
      tile("Pools lidos", String(ls.universe), "trending 1h/6h + volume 24h"),
      tile("Fora do corte", String(cutN), CUT_TEXT(ls.cut) || "—"),
      tile("Mais vendas / honeypot", `${ls.sells_dropped} / ${ls.honeypots}`, "vendas > compras em 5m e 15m"),
      tile("Candidatos", String((ls.candidates || []).length), "mais holders/h primeiro; empurrado por último")),
    ls.stand_down ? h("div", { class: "desk-strip warn", style: "cursor:default" }, h("i", { class: "dot warn" }),
      h("span", { class: "grow" }, `${ls.stand_down}: escaneado e registrado, nada foi ao JUDGE.`)) : null,
    ls.errors?.length ? h("p", { class: "agent-err", style: "margin:0" }, ls.errors.join(" · ")) : null,
    t);
}
function chiefCard(ls, g) {
  const sub = `Grok · ${num(g.calls_today, 0)} leitura${g.calls_today === 1 ? "" : "s"} hoje${g.max_calls_per_day ? ` de ${g.max_calls_per_day}` : ""} · US$ ${num(g.usd_total, 4)} no total`;
  const r = ls?.grok;
  let body;
  if (!r) body = emptyState("Nada para ler neste SCAN", ls?.stand_down ? "Mesa parada pelo dial." : "Sem candidatos ou book cheio.");
  else if (r.error || r.skipped) body = h("p", { class: r.error ? "agent-err" : "muted", style: "margin:0" }, r.error || r.skipped);
  else {
    const picks = (r.shortlist || []).map((lab) => h("li", {}, h("span", { class: "badge" }, lab),
      h("span", { class: "what" }, (r.names || {})[lab] || lab), h("span", { class: "ms" }, "")));
    body = [h("p", { style: "margin:0" }, r.summary),
      picks.length ? h("ul", { class: "checks" }, picks) : h("p", { class: "muted", style: "margin:0" }, "Nenhum vale uma chamada do Jev."),
      r.risks?.length ? h("ul", { class: "risks" }, r.risks.map((x) => h("li", {}, x))) : null];
  }
  return card("CHIEF · Grok lê o SCAN", sub, h("p", { class: "fine", style: "margin:0" },
    "Recebe só rótulos C1..Cn e números. Nome, símbolo e links do token nunca chegam ao Grok ou ao Jev."), body);
}
function judgeCard(ls, j, cfg) {
  const sub = `Jev · ${j.calls_today} chamada${j.calls_today === 1 ? "" : "s"} hoje${j.max_calls_per_day ? ` de ${j.max_calls_per_day}` : ""} · ${num(j.credits, 0)} créditos no total`;
  const r = ls?.jev;
  let body;
  if (!r) body = emptyState("Jev não foi chamado neste SCAN", "O Grok não separou ninguém ou a mesa estava parada.");
  else if (r.skipped || (r.error && !r.pick)) body = h("p", { class: r.error ? "agent-err" : "muted", style: "margin:0" }, r.skipped || r.error);
  else {
    const probs = Object.entries(r.probabilities || {}).sort((a, b) => b[1] - a[1]);
    const rows = probs.map(([lab, p]) => h("li", {},
      h("span", { class: `badge ${lab === r.pick ? "ok" : ""}` }, lab),
      h("span", { class: "what" }, lab === "NONE" ? "nenhum" : (r.names || {})[lab] || lab,
        h("div", { class: "bar", style: "margin-top:6px", role: "img", "aria-label": `${lab} ${num(p * 100, 0)}%` },
          h("i", { class: "p", style: `width:${Math.max(1, p * 100)}%` }))),
      h("span", { class: "ms" }, pct(p * 100, 0))));
    const picked = r.pick && r.pick !== "NONE";
    body = [h("p", { style: "margin:0" }, picked
      ? `Escolheu ${r.pick} ${(r.names || {})[r.pick] || ""}. Chance de +${num(cfg.tp, 0)}% antes de −${num(cfg.sl, 0)}%: ${r.p_target == null ? "—" : pct(r.p_target * 100, 0)}.`
      : `NONE: nenhum candidato forte o bastante hoje.`),
      h("ul", { class: "checks" }, rows),
      picked && r.size ? h("div", { class: "activity" }, h("span", { class: "tag" }, `SIZE · ${r.ticket ? usd(r.ticket) : "não entra"}`), r.size) : null,
      r.fill ? h("p", { class: "muted", style: "margin:0" }, `FILLS: ${r.fill} (simulado)`) : null,
      r.error ? h("p", { class: "agent-err", style: "margin:0" }, r.error) : null];
  }
  return card("JUDGE · Jev escolhe um ou nenhum", sub, body);
}
function memeVerdict(m) {
  const a = m.books.find((b) => b.name === "memes"), b = m.books.find((x) => x.name === "memes_top");
  if (!a || !b) return null;
  const need = 30, n = Math.min(a.performance.trades, b.performance.trades);
  let text, cls, tag;
  if (n < need) { text = "Amostra insuficiente. Qualquer diferença até aqui pode ser sorte."; cls = "warn"; tag = "aguardando"; }
  else if (a.performance.net_pnl > b.performance.net_pnl && (a.performance.profit_factor ?? 0) > (b.performance.profit_factor ?? 0)) {
    text = "Grok + Jev escolhem melhor que o #1 do SCAN em líquido e profit factor (simulado)."; cls = "ok"; tag = "passou";
  } else { text = "Grok + Jev não superam o #1 do SCAN. A IA não está agregando: parar ou mudar a hipótese."; cls = "bad"; tag = "parar"; }
  return h("section", { class: "card" }, head("Veredito dos memes", "Grok + Jev contra o #1 do SCAN, mesmas saídas", h("span", { class: `badge ${cls}` }, tag)),
    h("div", { class: "verdict" },
      h("div", {}, h("div", { class: "progress-label" }, h("span", {}, "Trades fechados (o menor dos dois)"), h("span", { class: "mono" }, `${n}/${need}`)),
        h("div", { class: "bar", style: "margin-top:8px", role: "progressbar", "aria-valuenow": n, "aria-valuemax": need },
          h("i", { class: "p", style: `width:${Math.min(100, (n / need) * 100)}%` }))),
      h("p", {}, text)));
}
function renderMemes(d) {
  const m = d.memes;
  if (!m) return [card("Memes", null, emptyState("Sleeve de memes desligado", "MEME_SLEEVE=0 no .env"))];
  const tile = (k, v, sub) => h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v), sub ? h("div", { class: "s" }, sub) : null);
  const [cls, text] = memeState(m, d.desk);
  const ls = m.last_scan, cfg = m.config;
  const strip = h("div", { class: `desk-strip ${cls === "ok" ? "" : cls}`, style: "cursor:default" }, h("i", { class: `dot ${cls}` }),
    h("span", { class: "grow" }, text), h("span", { class: "go" }, ls ? `SCAN ${ago(ls.ts)}` : "sem SCAN"));
  const books = m.books.map((b) => tile([swatch(b.name), " ", label(b.name)], usd(b.equity),
    `${signedText(b.return_pct, (v) => pct(v))} · ${b.positions.length} abertas · ${b.performance.trades} trades`));
  const chart = equityChart(m.books, { height: 220, fmt: usd, wide: true });
  const positions = m.books.flatMap((b) => b.positions);
  const trades = m.trades.slice(0, 30);
  const control = ls?.control;
  return [
    strip,
    h("section", { class: "card" }, head("Bancas simuladas", `US$ ${num(cfg.bank, 0)} cada · ${cfg.nets.join(" + ")} · nada aqui é dinheiro real`, chart.legend || null),
      h("div", { class: "grid tiles" }, books.length ? books : [tile("Memes", "—", "aparece depois do primeiro SCAN")]), chart),
    scanCard(ls, tile),
    h("div", { class: "split" }, chiefCard(ls, m.grok), judgeCard(ls, m.jev, cfg)),
    control ? card("Controle · #1 do SCAN", "sem Grok e sem Jev, ticket fixo 6% × dial, mesmas saídas", h("p", { style: "margin:0" }, control)) : null,
    h("div", { class: "split" },
      flush("Posições abertas", `máximo ${cfg.max_positions} por book · RISK confere a cada 5 min`, null,
        list(positions.map(positionItem), emptyState("Nenhuma posição aberta", null))),
      flush("Trades fechados", `alvo +${num(cfg.tp, 0)}% · stop −${num(cfg.sl, 0)}% · ${cfg.hold_h} h · volume 6h < 20% da média · liquidez < 25%`, null,
        list(trades.map(tradeItem), emptyState("Nenhum trade fechado ainda", null)))),
    memeVerdict(m),
  ].filter(Boolean);
}

// ---------------------------------------------------------------- Desempenho
function verdict(d) {
  const jev = d.books.find((b) => b.name === "jev");
  if (!jev) return card("Veredito", null, h("p", { class: "muted", style: "margin:0" }, "Jev desligado (sem JEV_API_KEY). Comparando só baseline × aleatório."));
  const n = jev.performance.trades, need = 100;
  const others = d.books.filter((b) => b.name !== "jev");
  const beats = others.every((o) => jev.performance.net_pnl > o.performance.net_pnl &&
    (jev.performance.profit_factor ?? 0) > (o.performance.profit_factor ?? 0));
  let text, cls, tag;
  if (n < need) { text = `Amostra insuficiente. Qualquer resultado até aqui pode ser sorte.`; cls = "warn"; tag = "aguardando"; }
  else if ((jev.performance.expectancy ?? 0) <= 0) { text = "Critério de parada atingido: expectativa líquida ≤ 0. Parar ou mudar a hipótese."; cls = "bad"; tag = "parar"; }
  else if (!beats) { text = "Critério de parada atingido: Jev não supera baseline e aleatório. Parar ou mudar a hipótese."; cls = "bad"; tag = "parar"; }
  else { text = "Jev supera baseline e aleatório em líquido e profit factor (simulado)."; cls = "ok"; tag = "passou"; }
  return h("section", { class: "card" }, head("Veredito", "critério definido antes dos resultados", h("span", { class: `badge ${cls}` }, tag)),
    h("div", { class: "verdict" },
      h("div", {}, h("div", { class: "progress-label" }, h("span", {}, "Trades fechados do Jev"), h("span", { class: "mono" }, `${n}/${need}`)),
        h("div", { class: "bar", style: "margin-top:8px", role: "progressbar", "aria-valuenow": n, "aria-valuemax": need },
          h("i", { class: "p", style: `width:${Math.min(100, (n / need) * 100)}%` }))),
      h("p", {}, text)));
}
function renderPerf(d) {
  if (!d.books.length) return [card("Sem dados", null, emptyState("Nenhuma carteira ainda", null))];
  const rows = d.books.map((b) => ({ name: b.name, ...b.performance, dd: b.max_drawdown_pct }));
  const comp = table([
    { t: "Carteira", v: (r) => who(r.name) },
    { t: "Trades", num: 1, v: (r) => String(r.trades) },
    { t: "Líquido", num: 1, v: (r) => signed(r.net_pnl) },
    { t: "Retorno", num: 1, v: (r) => signed(r.return_pct, (v) => pct(v)) },
    { t: "Acerto", num: 1, v: (r) => pct(r.win_rate, 1) },
    { t: "Profit factor", num: 1, v: (r) => num(r.profit_factor) },
    { t: "Expectativa", num: 1, v: (r) => signed(r.expectancy) },
    { t: "Ganho médio", num: 1, v: (r) => brl(r.avg_win) },
    { t: "Perda média", num: 1, v: (r) => brl(r.avg_loss) },
    { t: "Taxas", num: 1, v: (r) => brl(r.fees) },
    { t: "DD máx.", num: 1, v: (r) => pct(r.dd) },
  ], rows);
  const books = [["all", "Todas", null], ...d.books.map((b) => [b.name, label(b.name), b.name])];
  if (!books.some(([v]) => v === st.perfBook)) st.perfBook = "all";
  const trades = [...d.trades].sort(byExit).filter((t) => st.perfBook === "all" || t.book === st.perfBook);
  const shown = trades.slice(0, st.histLimit);
  const wins = trades.filter((t) => t.pnl > 0).length;
  const chart = equityChart(d.books, { height: 300, focus: st.perfBook === "all" ? null : st.perfBook, wide: true });
  return [
    verdict(d),
    card("Comparação", `taxa ${pct(d.limits.fee_rate * 100, 2)}/lado · slippage ${num(d.limits.slippage_bps, 0)} bps · valores líquidos`, comp),
    h("section", { class: "card" }, head("Curva de patrimônio", null, chart.legend || null), chart),
    flush("Histórico completo", `${trades.length} trades · ${wins} vitórias · ${trades.length - wins} derrotas`,
      seg(books, st.perfBook, (v) => { st.perfBook = v; st.histLimit = 60; render(); }),
      list(shown.map(tradeItem), emptyState("Nenhum trade fechado ainda", null)),
      trades.length > shown.length ? h("button", { class: "more", onclick: () => { st.histLimit += 100; render(); } },
        `Mostrar mais (${trades.length - shown.length})`) : null),
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
  } catch (e) { toast(`Erro: ${e.message}`); }
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
    h("section", { class: "card" }, head("Operação", `ambiente ${d.mode}`),
      setting("Novas entradas", sy.paused ? "Pausadas. Saídas por stop, alvo e tempo continuam funcionando."
        : "Liberadas. O bot abre e fecha posições sozinho.",
      sy.paused
        ? h("button", { class: "btn primary", onclick: () => control("resume", { title: "Retomar entradas?", text: "O bot volta a abrir posições sozinho.", word: "RETOMAR", okLabel: "Retomar" }) }, "Retomar")
        : h("button", { class: "btn", onclick: () => control("pause", { title: "Pausar novas entradas?", text: "Posições abertas continuam com stop e alvo.", okLabel: "Pausar" }) }, "Pausar")),
      h("p", { class: "fine" }, "O painel só freia. Não há como sacar, ver chaves, mudar limites ou ligar dinheiro real por aqui.")),
    h("section", { class: "card danger-zone" }, head("Kill switch", sy.kill ? "ativo" : "desligado"),
      setting(sy.kill ? "Kill switch ativo" : "Parada de emergência",
        sy.kill ? "Nenhuma entrada nova em nenhum ambiente até você desligar." : "Bloqueia novas entradas em todos os ambientes de uma vez.",
        sy.kill
          ? h("button", { class: "btn", onclick: () => control("kill_off", { title: "Desligar o kill switch?", text: "Novas entradas voltam a ser permitidas.", word: "DESLIGAR", okLabel: "Desligar" }) }, "Desligar")
          : h("button", { class: "btn danger", onclick: () => control("kill_on", { title: "Ativar o kill switch?", text: "Bloqueia novas entradas em todos os ambientes.", okLabel: "Ativar", danger: true }) }, "Ativar kill switch"))),
    h("div", { class: "split" },
      card("Limites de risco", "somente leitura · mudam no .env", h("dl", { class: "facts" },
        h("dt", {}, "Capital por carteira"), h("dd", {}, brl(L.capital)),
        h("dt", {}, "Máx. por entrada"), h("dd", {}, brl(L.entry)),
        h("dt", {}, "Exposição máxima"), h("dd", {}, brl(L.max_exposure)),
        h("dt", {}, "Perda diária máxima"), h("dd", {}, brl(L.daily_loss)),
        h("dt", {}, "Posições por par"), h("dd", {}, String(L.max_positions_per_symbol)),
        h("dt", {}, "Stop / alvo"), h("dd", {}, `${num(L.stop_atr)}× / ${num(L.target_atr)}× ATR`),
        h("dt", {}, "Saída por tempo"), h("dd", {}, `${L.max_hold_candles} candles`),
        h("dt", {}, "Taxa / slippage"), h("dd", {}, `${pct(L.fee_rate * 100)} / ${num(L.slippage_bps, 0)} bps`))),
      card("Ambiente", "o seletor no topo só muda o que o painel mostra", h("dl", { class: "facts" },
        h("dt", {}, "Exibindo"), h("dd", {}, d.mode),
        h("dt", {}, "Corretora"), h("dd", {}, d.exchange),
        h("dt", {}, "Pares"), h("dd", { class: "mono" }, d.symbols.join(", ")),
        h("dt", {}, "Timeframe"), h("dd", {}, d.timeframe),
        h("dt", {}, "Sinal de vida"), h("dd", {}, ago(sy.heartbeat))))),
    flush("Logs", "últimos eventos", null, list(events, emptyState("Sem eventos", null))),
    h("div", { style: "display:flex;justify-content:center" }, h("button", { class: "btn ghost", onclick: async () => {
      try { await api("/api/logout", { method: "POST", body: "{}" }); } catch (e) { /* ignora */ }
      st.csrf = null; showLogin();
    } }, "Sair do painel")),
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

// ---------------------------------------------------------------- fundo: personagem em halftone
// rafs-art.png é um mapa de tons pequeno (cinza = intensidade); os pontos são desenhados aqui, nítidos em qualquer tela.
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
