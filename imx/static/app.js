/* iMessage Explorer interface.
 *
 * Message text is other people's writing and is never trusted: it is always
 * placed on the page as plain text (textContent), never as HTML.
 */
"use strict";

const TOKEN = document.querySelector('meta[name="imx-token"]').content;
const view = document.getElementById("view");

const state = {
  tab: "home",
  person: "",
  filters: { contact: "", date_from: "", date_to: "", direction: "", scope: "", include_junk: "" },
  search: { q: "", sort: "newest", offset: 0 },
  bucket: "month",
  keyword: "",
  ask: { q: "", question: "", strategy: "recent" },
  meta: null,
};
const PAGE = 50;
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/* ---------- small helpers ---------- */

function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "value") node.value = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

function svg(tag, attrs, ...children) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  for (const child of children) node.append(child);
  return node;
}

const fmt = (n) => (n === null || n === undefined ? "–" : Number(n).toLocaleString());

function duration(seconds) {
  if (seconds === null || seconds === undefined) return "not enough replies";
  if (seconds < 90) return seconds + " sec";
  if (seconds < 5400) return Math.round(seconds / 60) + " min";
  if (seconds < 172800) return (seconds / 3600).toFixed(1) + " hours";
  return (seconds / 86400).toFixed(1) + " days";
}

function when(ts) {
  return new Date(ts * 1000).toLocaleString([], { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

async function request(path, options) {
  const response = await fetch(path, Object.assign({ headers: { "X-IMX-Token": TOKEN, "Content-Type": "application/json" } }, options));
  let data = {};
  try { data = await response.json(); } catch (e) { /* non-JSON error body */ }
  if (!response.ok) throw new Error(data.error || "Request failed (" + response.status + ").");
  return data;
}
function get(path, params) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(Object.assign({}, state.filters, params || {}))) {
    if (value !== "" && value !== null && value !== undefined) query.set(key, value);
  }
  return request(path + "?" + query.toString());
}
const post = (path, body) => request(path, { method: "POST", body: JSON.stringify(body) });

function show(...nodes) { view.replaceChildren(...nodes.flat(Infinity).filter(Boolean)); }
function loading(text) { show(h("p", { class: "muted", text: text || "Working…" })); }
function failure(error) { show(h("div", { class: "panel" }, h("p", { class: "error", text: error.message }))); }
/* The exact rule behind a number. Kept one click away so screens stay easy to scan. */
function definition(text) { return h("details", { class: "definition" }, h("summary", { text: "How this is measured" }), h("p", { text })); }
function card(num, label) { return h("div", { class: "card" }, h("div", { class: "num", text: num }), h("div", { class: "lbl", text: label })); }

function legend() {
  return h("div", { class: "legend" },
    h("span", {}, h("i", { class: "swatch sent" }), "Sent by me"),
    h("span", {}, h("i", { class: "swatch received" }), "Received"));
}

/* Horizontal bars. rows: [{name, sent, received}] or [{name, total}]. */
function bars(rows, onClick, fixedMax) {
  const max = fixedMax || Math.max(1, ...rows.map((r) => r.total !== undefined ? r.total : r.sent + r.received));
  return h("div", { class: "bars" }, rows.map((r) => {
    const total = r.total !== undefined ? r.total : r.sent + r.received;
    const track = h("div", { class: "bar-track" });
    const sent = h("div", { class: "bar-sent" });
    const received = h("div", { class: "bar-received" });
    if (r.sent !== undefined) {
      sent.style.width = (100 * r.sent / max) + "%";
      received.style.width = (100 * (total - r.sent) / max) + "%";
      track.append(sent, received);
    } else {
      sent.style.width = (100 * total / max) + "%";
      track.append(sent);
    }
    const name = onClick
      ? h("button", { class: "link name", title: r.name, onclick: () => onClick(r), text: r.name })
      : h("span", { class: "name", title: r.name, text: r.name });
    return h("div", { class: "bar-row" }, name, track, h("span", { class: "val", text: r.label || fmt(total) }));
  }));
}

/* Stacked column chart over time. buckets: [{period, sent, received}] */
function columns(buckets, options) {
  const single = options && options.single;
  if (!buckets.length) return h("p", { class: "muted", text: "No messages in this selection." });
  const W = 1000, H = 240, left = 44, bottom = 22, top = 8;
  const max = Math.max(1, ...buckets.map((b) => b.sent + b.received));
  const step = (W - left) / buckets.length;
  const barWidth = Math.max(1, Math.min(step * 0.82, 46));
  const scale = (H - bottom - top) / max;
  const chart = svg("svg", { class: "chart", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Messages over time" });
  for (const fraction of [0, 0.5, 1]) {
    const y = H - bottom - fraction * (H - bottom - top);
    chart.append(svg("line", { x1: left, x2: W, y1: y, y2: y }));
    const label = svg("text", { x: left - 6, y: y + 4, "text-anchor": "end" });
    label.textContent = fmt(Math.round(max * fraction));
    chart.append(label);
  }
  const labelEvery = Math.max(1, Math.ceil(buckets.length / 12));
  buckets.forEach((b, i) => {
    const x = left + i * step + (step - barWidth) / 2;
    const sentH = b.sent * scale, receivedH = b.received * scale;
    const group = svg("g", {});
    const tip = svg("title", {});
    tip.textContent = single ? b.period + ": " + fmt(b.sent) + " " + single : b.period + ": " + fmt(b.sent) + " sent, " + fmt(b.received) + " received";
    group.append(tip,
      svg("rect", { class: "received", x, y: H - bottom - receivedH - sentH, width: barWidth, height: receivedH }),
      svg("rect", { class: "sent", x, y: H - bottom - sentH, width: barWidth, height: sentH }));
    chart.append(group);
    if (i % labelEvery === 0) {
      const text = svg("text", { x: x + barWidth / 2, y: H - 6, "text-anchor": "middle" });
      text.textContent = b.period;
      chart.append(text);
    }
  });
  return h("div", {}, single ? null : legend(), chart);
}

/* ---------- message rendering ---------- */

function searchTerms(q) {
  const terms = [];
  for (const m of (q || "").matchAll(/"([^"]+)"|(\S+)/g)) {
    let term = m[1] || m[2];
    if (!m[1] && (term.toUpperCase() === "OR" || term.startsWith("-"))) continue;
    term = term.replace(/["*]/g, "").trim();
    if (term.length > 1) terms.push(term);
  }
  return terms;
}

function highlighted(text, terms) {
  const node = h("div", { class: "text" });
  if (!terms.length) { node.textContent = text; return node; }
  const pattern = new RegExp("(" + terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "gi");
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    node.append(text.slice(last, m.index), h("mark", { text: m[0] }));
    last = m.index + m[0].length;
  }
  node.append(text.slice(last));
  return node;
}

function messageNode(m, terms, options) {
  const opts = options || {};
  const who = m.is_group ? m.sender + " in " + m.counterpart : (m.from_me ? "Me to " + m.counterpart : m.sender);
  const body = m.text ? highlighted(m.text, terms || []) : h("div", { class: "text muted", text: m.has_attachment ? "[attachment]" : "[no text]" });
  const node = h("div", { class: "msg" + (m.from_me ? " mine" : "") + (opts.target ? " target" : "") },
    h("div", { class: "meta" }, h("span", { class: "who", text: who }), h("span", { text: when(m.ts) }),
      m.service && m.service !== "iMessage" ? h("span", { text: m.service }) : null),
    body);
  if (opts.withContext) {
    const holder = h("div", {});
    const toggle = h("button", { class: "link small", text: "Show conversation", onclick: async () => {
      if (holder.childNodes.length) { holder.replaceChildren(); toggle.textContent = "Show conversation"; return; }
      try {
        const data = await get("/api/context", { id: m.id });
        holder.replaceChildren(h("div", { class: "context" },
          data.messages.map((c) => messageNode(c, terms, { target: c.id === data.target_id }))));
        toggle.textContent = "Hide conversation";
      } catch (error) { holder.replaceChildren(h("p", { class: "error", text: error.message })); }
    } });
    node.append(toggle, holder);
  }
  return node;
}

/* ---------- screens ---------- */

async function renderSearch() {
  const s = state.search;
  const input = h("input", { type: "search", value: s.q, placeholder: 'Words, "exact phrase", flight OR airport, -exclude, vacat*', "aria-label": "Search text" });
  const sort = h("select", { "aria-label": "Sort order" },
    h("option", { value: "newest", text: "Newest first" }), h("option", { value: "oldest", text: "Oldest first" }),
    h("option", { value: "relevance", text: "Best match first" }));
  sort.value = s.sort;
  const run = () => { s.q = input.value; s.sort = sort.value; s.offset = 0; renderSearch(); };
  input.addEventListener("keydown", (event) => { if (event.key === "Enter") run(); });
  sort.addEventListener("change", run);
  const form = h("div", { class: "panel" }, h("div", { class: "row grow" }, input, sort, h("button", { class: "primary", text: "Search", onclick: run })));
  const results = h("div", { class: "panel" }, h("p", { class: "muted", text: "Searching…" }));
  show(form, results);
  input.focus();
  try {
    const data = await get("/api/search", { q: s.q, sort: s.sort, limit: PAGE, offset: s.offset });
    const terms = searchTerms(s.q);
    const from = data.total ? s.offset + 1 : 0;
    const head = h("div", { class: "row" },
      h("strong", { text: fmt(data.total) + (data.total === 1 ? " message" : " messages") }),
      data.total ? h("span", { class: "muted small", text: "showing " + fmt(from) + " to " + fmt(s.offset + data.results.length) }) : null,
      s.q ? h("button", { class: "quiet", text: "Chart over time", onclick: () => { state.keyword = s.q; setTab("words"); } }) : null,
      data.total ? h("button", { class: "quiet", text: "Ask AI about these", onclick: () => { state.ask.q = s.q; setTab("ask"); } }) : null,
      data.total ? h("button", { class: "quiet", text: "Export CSV", onclick: exportCsv }) : null);
    const pager = h("div", { class: "row" },
      h("button", { text: "Previous", disabled: s.offset === 0, onclick: () => { s.offset = Math.max(0, s.offset - PAGE); renderSearch(); } }),
      h("button", { text: "Next", disabled: s.offset + PAGE >= data.total, onclick: () => { s.offset += PAGE; renderSearch(); } }));
    results.replaceChildren(head, h("div", { class: "spacer" }),
      h("div", {}, data.results.map((m) => messageNode(m, terms, { withContext: true }))),
      data.total > PAGE ? pager : null);
  } catch (error) { results.replaceChildren(h("p", { class: "error", text: error.message })); }
}

async function exportCsv() {
  try {
    const data = await get("/api/search", { q: state.search.q, sort: state.search.sort, limit: 5000, offset: 0 });
    const cell = (value) => '"' + String(value === null || value === undefined ? "" : value).replace(/"/g, '""') + '"';
    const lines = [["date", "sender", "conversation", "group", "service", "text"].join(",")];
    for (const m of data.results) {
      lines.push([new Date(m.ts * 1000).toISOString(), m.sender, m.counterpart, m.is_group ? "yes" : "no", m.service || "", m.text].map(cell).join(","));
    }
    const url = URL.createObjectURL(new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv" }));
    const link = h("a", { href: url, download: "messages.csv" });
    document.body.append(link); link.click(); link.remove(); URL.revokeObjectURL(url);
    if (data.total > data.results.length) alert("Exported the first " + fmt(data.results.length) + " of " + fmt(data.total) + " messages. Narrow the filters to export the rest.");
  } catch (error) { alert(error.message); }
}

async function renderPeople() {
  loading();
  const data = await get("/api/stats/people");
  const t = data.totals;
  show(
    h("div", { class: "cards" }, card(fmt(t.messages), "messages"), card(fmt(t.sent), "sent by me"), card(fmt(t.received), "received"),
      card(fmt(t.people), "people"), card(fmt(t.groups), "group chats"), card(fmt(t.active_days), "days with messages")),
    h("div", { class: "panel" }, h("h2", { text: "Who you message most" }), definition(data.definition),
      t.first_day ? h("p", { class: "muted small", text: "From " + t.first_day + " to " + t.last_day + ". Click a name to filter every screen to that conversation." }) : null,
      legend(),
      bars(data.rows.map((r) => ({ name: r.name + (r.is_group ? " (group)" : ""), sent: r.sent, received: r.received, label: fmt(r.total) + " · " + r.share + "%", raw: r.name })),
        (r) => { setFilter("contact", r.raw); })));
}

async function renderTrends() {
  loading();
  const data = await get("/api/stats/trends", { bucket: state.bucket });
  const picker = h("select", { "aria-label": "Period" }, ["day", "week", "month", "year"].map((b) => h("option", { value: b, text: "By " + b })));
  picker.value = state.bucket;
  picker.addEventListener("change", () => { state.bucket = picker.value; renderTrends(); });
  const peak = Math.max(1, ...data.heatmap.flat());
  const heat = h("table", { class: "heat" },
    h("tr", {}, h("th", {}), Array.from({ length: 24 }, (_, hour) => h("th", { text: hour % 3 === 0 ? (hour === 0 ? "12a" : hour < 12 ? hour + "a" : hour === 12 ? "12p" : (hour - 12) + "p") : "" }))),
    data.heatmap.map((row, day) => h("tr", {}, h("th", { class: "day", text: DAYS[day] }), row.map((count, hour) => {
      const fill = h("i", {});
      fill.style.opacity = count ? Math.max(0.08, count / peak) : 0;
      return h("td", { class: "cell", title: DAYS[day] + " " + hour + ":00, " + fmt(count) + " messages" }, fill);
    }))));
  show(
    h("div", { class: "panel" }, h("div", { class: "row" }, h("h2", { text: "Messages over time" }), picker), definition(data.definition), columns(data.buckets)),
    h("div", { class: "grid2" },
      h("div", { class: "panel" }, h("h2", { text: "Busiest hours and weekdays" }), h("div", { class: "scroll" }, heat)),
      h("div", { class: "panel" }, h("h2", { text: "Busiest days" }),
        bars(data.busiest_days.map((d) => ({ name: d.day, total: d.total })), (r) => { state.filters.date_from = r.name; state.filters.date_to = r.name; syncFilterInputs(); setTab("search"); }))));
}

async function renderReplies() {
  loading();
  const [data, even, read, direct] = await Promise.all([get("/api/stats/response"), get("/api/balance"), get("/api/read-times"), get("/api/direct-replies")]);
  const o = data.overall;
  const started = o.started_by_me + o.started_by_them;
  const share = (n) => started ? Math.round(100 * n / started) + "%" : "–";
  show(
    h("div", { class: "cards" }, card(duration(o.my_median_seconds), "my typical reply time"), card(duration(o.their_median_seconds), "their typical reply time"),
      card(share(o.started_by_me), "conversations I start"), card(share(o.started_by_them), "conversations they start")),
    h("div", { class: "panel" }, h("h2", { text: "Reply patterns by person" }), definition(data.definition),
      h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "Person" }), h("th", { class: "n", text: "Messages" }), h("th", { class: "n", text: "My reply" }),
          h("th", { class: "n", text: "Their reply" }), h("th", { class: "n", text: "I start" }), h("th", { class: "n", text: "They start" }), h("th", { class: "n", text: "Longest silence" }))),
        h("tbody", {}, data.rows.map((r) => h("tr", {},
          h("td", {}, h("button", { class: "link", text: r.name, onclick: () => setFilter("contact", r.name) })),
          h("td", { class: "n", text: fmt(r.messages) }), h("td", { class: "n", text: duration(r.my_median_seconds) }),
          h("td", { class: "n", text: duration(r.their_median_seconds) }), h("td", { class: "n", text: fmt(r.started_by_me) }),
          h("td", { class: "n", text: fmt(r.started_by_them) }), h("td", { class: "n", text: r.longest_gap_days ? r.longest_gap_days + " days" : "–" }))))))),
    h("div", { class: "panel" }, h("h2", { text: "Conversation balance" }), definition(even.definition),
      h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "Person" }), h("th", { class: "n", text: "My share of words" }), h("th", { class: "n", text: "My avg words" }), h("th", { class: "n", text: "Their avg words" }),
          h("th", { class: "n", text: "I send 3+ in a row" }), h("th", { class: "n", text: "They send 3+ in a row" }), h("th", { class: "n", text: "My questions" }), h("th", { class: "n", text: "Their questions" }),
          h("th", { class: "n", text: "Left unanswered by me" }), h("th", { class: "n", text: "Left unanswered by them" }))),
        h("tbody", {}, even.rows.map((r) => h("tr", {},
          h("td", {}, personLink(r.name)), h("td", { class: "n", text: r.my_word_share === null ? "–" : r.my_word_share + "%" }), h("td", { class: "n", text: r.my_average_words === null ? "–" : r.my_average_words }),
          h("td", { class: "n", text: r.their_average_words === null ? "–" : r.their_average_words }), h("td", { class: "n", text: fmt(r.my_runs) }), h("td", { class: "n", text: fmt(r.their_runs) }),
          h("td", { class: "n", text: fmt(r.my_questions) }), h("td", { class: "n", text: fmt(r.their_questions) }), h("td", { class: "n", text: fmt(r.unanswered_by_me) }), h("td", { class: "n", text: fmt(r.unanswered_by_them) }))))))),
    h("div", { class: "panel" }, h("h2", { text: "How long messages wait to be read" }), definition(read.definition),
      h("p", { class: "small muted", text: "This Mac recorded a read time for " + read.coverage.received_share + "% of messages you received and " + read.coverage.sent_share + "% of messages you sent." }),
      read.me_reading_them.messages || read.them_reading_me.messages ? h("div", {},
        h("div", { class: "cards" }, card(read.me_reading_them.messages ? duration(read.me_reading_them.median_seconds) : "–", "I typically read theirs in"),
          card(read.me_reading_them.within_5_minutes === null ? "–" : read.me_reading_them.within_5_minutes + "%", "of theirs I read within 5 minutes"),
          card(read.them_reading_me.messages ? duration(read.them_reading_me.median_seconds) : "–", "they typically read mine in"),
          card(read.me_reading_them.over_a_day === null ? "–" : read.me_reading_them.over_a_day + "%", "of theirs I leave over a day")),
        read.rows.length ? h("div", { class: "scroll" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", { text: "Person" }), h("th", { class: "n", text: "I read theirs in" }), h("th", { class: "n", text: "Within 5 min" }), h("th", { class: "n", text: "Over a day" }),
            h("th", { class: "n", text: "They read mine in" }), h("th", { class: "n", text: "Read times recorded" }))),
          h("tbody", {}, read.rows.map((r) => h("tr", {}, h("td", {}, personLink(r.name)),
            h("td", { class: "n", text: r.me_reading_them.messages ? duration(r.me_reading_them.median_seconds) : "–" }), h("td", { class: "n", text: r.me_reading_them.within_5_minutes === null ? "–" : r.me_reading_them.within_5_minutes + "%" }),
            h("td", { class: "n", text: r.me_reading_them.over_a_day === null ? "–" : r.me_reading_them.over_a_day + "%" }),
            h("td", { class: "n", text: r.them_reading_me.messages ? duration(r.them_reading_me.median_seconds) : "not shared" }),
            h("td", { class: "n", text: fmt(r.me_reading_them.messages + r.them_reading_me.messages) })))))) : null)
        : h("p", { class: "muted", text: "No read times are recorded for this selection." })),
    h("div", { class: "panel" }, h("h2", { text: "Direct replies, edits and unsends" }), definition(direct.definition), legend(),
      bars([["direct_replies", "Direct replies"], ["edited", "Edited messages"], ["unsent", "Unsent messages"]].map(([k, label]) =>
        ({ name: label, sent: direct.totals[k].me, received: direct.totals[k].them, label: fmt(direct.totals[k].me) + " me · " + fmt(direct.totals[k].them) + " them" })))));
}

async function renderWords() {
  loading("Counting words and phrases…");
  const [data, longer] = await Promise.all([get("/api/stats/words"), get("/api/phrases")]);
  const toSearch = (r) => { state.search.q = r.name.includes(" ") ? '"' + r.name + '"' : r.name; state.search.offset = 0; setTab("search"); };
  const list = (title, items, clickable) => h("div", { class: "panel" }, h("h2", { text: title }),
    items.length ? bars(items.slice(0, 30).map((i) => ({ name: i.term, total: i.count })), clickable ? toSearch : null) : h("p", { class: "muted", text: "None found." }));
  const input = h("input", { type: "search", value: state.keyword, placeholder: "A word, phrase or search to chart", "aria-label": "Keyword to chart" });
  const chartHolder = h("div", {});
  const draw = async () => {
    state.keyword = input.value.trim();
    if (!state.keyword) { chartHolder.replaceChildren(); return; }
    chartHolder.replaceChildren(h("p", { class: "muted", text: "Counting…" }));
    try {
      const trend = await get("/api/stats/keyword", { q: state.keyword, bucket: state.bucket });
      chartHolder.replaceChildren(
        h("p", { text: fmt(trend.total) + " matching messages." }), columns(trend.buckets),
        h("h3", { text: "Who it comes up with" }), legend(),
        bars(trend.by_person.map((p) => ({ name: p.name, sent: p.sent, received: p.received })), (r) => setFilter("contact", r.name)));
    } catch (error) { chartHolder.replaceChildren(h("p", { class: "error", text: error.message })); }
  };
  input.addEventListener("keydown", (event) => { if (event.key === "Enter") draw(); });
  show(
    h("div", { class: "panel" }, h("h2", { text: "A word or phrase over time" }),
      h("div", { class: "row grow" }, input, h("button", { class: "primary", text: "Chart", onclick: draw })), h("div", { class: "spacer" }), chartHolder),
    definition(data.definition + " " + fmt(data.messages_analyzed) + " messages analysed."),
    h("div", { class: "grid3" }, list("Most-used words", data.words, true), list("Two-word phrases", data.phrases, true), list("Most-used emoji", data.emoji, false)),
    h("div", { class: "panel" }, h("h2", { text: "Longer phrases" }), definition(longer.definition + " Phrases must appear at least " + longer.minimum_count + " times."),
      h("div", { class: "grid2" }, [["3", "Three words"], ["4", "Four words"], ["5", "Five words"], ["6+", "Six words or more"]].map(([key, title]) =>
        h("div", {}, h("h3", { text: title }), termList((longer.by_length[key] || []).slice(0, 20), true))))));
  if (state.keyword) draw();
}

async function renderTopics() {
  loading();
  const data = await get("/api/topics");
  const name = h("input", { placeholder: "Topic name, for example Travel", "aria-label": "Topic name" });
  const terms = h("input", { placeholder: "Words and phrases, separated by commas", "aria-label": "Topic words" });
  const message = h("p", { class: "error" });
  const save = async () => {
    try { await post("/api/topics/save", { name: name.value, terms: terms.value.split(",") }); renderTopics(); }
    catch (error) { message.textContent = error.message; }
  };
  show(
    h("div", { class: "panel" }, h("h2", { text: "Saved topics" }),
      definition("A topic is a named list of words and phrases. A message matches a topic when it contains any of them. Counts respect the filters above."),
      h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "Topic" }), h("th", { class: "n", text: "Messages" }), h("th", { text: "Words" }), h("th", {}))),
        h("tbody", {}, data.topics.map((t) => h("tr", {},
          h("td", {}, h("button", { class: "link", text: t.name, onclick: () => { state.search.q = t.query; state.search.offset = 0; setTab("search"); } })),
          h("td", { class: "n", text: fmt(t.total) }),
          h("td", {}, h("div", { class: "chips" }, t.terms.map((term) => h("span", { class: "chip", text: term })))),
          h("td", {}, h("div", { class: "row" },
            h("button", { class: "quiet small", text: "Chart", onclick: () => { state.keyword = t.query; setTab("words"); } }),
            h("button", { class: "quiet small", text: "Edit", onclick: () => { name.value = t.name; terms.value = t.terms.join(", "); name.focus(); } }),
            h("button", { class: "quiet small", text: "Delete", onclick: async () => { if (confirm('Delete the topic "' + t.name + '"?')) { await post("/api/topics/delete", { name: t.name }); renderTopics(); } } }))))))))),
    h("div", { class: "panel" }, h("h2", { text: "Add or change a topic" }),
      h("div", { class: "row grow" }, name, terms, h("button", { class: "primary", text: "Save topic", onclick: save })), message,
      h("p", { class: "muted small", text: "Saving a topic with an existing name replaces it. The Ask AI screen can also suggest topics from your messages." })));
}

function aiSetup(ai) {
  if (ai.key_found) return null;
  return keyPanel(async () => { await loadShell(); setTab("ask"); });
}

async function renderAsk() {
  const a = state.ask;
  const ai = state.meta.ai;
  const q = h("input", { type: "search", value: a.q, placeholder: "Optional: only messages matching this search", "aria-label": "Search text for AI" });
  const question = h("textarea", { placeholder: "Your question, for example: What did we decide about the June trip?", "aria-label": "Question" });
  question.value = a.question;
  const strategy = h("select", { "aria-label": "Which messages to send if there are too many" },
    h("option", { value: "recent", text: "Most recent" }), h("option", { value: "oldest", text: "Oldest" }), h("option", { value: "spread", text: "Spread evenly across the period" }));
  strategy.value = a.strategy;
  const out = h("div", {});
  const sync = () => { a.q = q.value; a.question = question.value; a.strategy = strategy.value; };
  const body = (extra) => Object.assign({ q: a.q, filters: state.filters, strategy: a.strategy }, extra || {});

  const previewThen = async (label, action) => {
    sync();
    out.replaceChildren(h("p", { class: "muted", text: "Checking what would be sent…" }));
    try {
      const p = await post("/api/ai/preview", body());
      if (!p.would_send) { out.replaceChildren(h("p", { text: "No messages match, so there is nothing to send." })); return; }
      const confirmButton = h("button", { class: "primary", text: label, disabled: !ai.key_found, onclick: async () => {
        out.replaceChildren(h("p", { class: "muted", text: "Waiting for the AI. This can take a minute…" }));
        try { await action(); } catch (error) { out.replaceChildren(h("p", { class: "error", text: error.message })); }
      } });
      out.replaceChildren(h("div", { class: "panel" }, h("h2", { text: "This is what would leave your Mac" }),
        h("div", { class: "cards" }, card(fmt(p.would_send), "messages to send"), card(fmt(p.total_matching), "messages matching"),
          card("about $" + p.estimated_input_cost_usd.toFixed(2), "estimated cost to read them"), card(p.first_day + " to " + p.last_day, "date range")),
        p.not_sent ? h("div", { class: "notice", text: fmt(p.not_sent) + " matching messages will NOT be included, because one request is limited to " + fmt(p.limits.messages) + " messages or " + fmt(p.limits.characters) + " characters. The AI will only see the " + (a.strategy === "spread" ? "evenly spread sample" : a.strategy + " ones") + ". Narrow the filters or the search to cover everything." }) : null,
        h("p", { class: "small muted", text: "Conversations included:" }), h("div", { class: "chips" }, p.people.map((name) => h("span", { class: "chip", text: name }))),
        h("div", { class: "spacer" }),
        h("p", { class: "small muted", text: "The text of these messages, including what other people wrote, will be sent to Anthropic's " + ai.model + " model. The cost is a rough estimate for reading the messages; the answer adds a little more." }),
        confirmButton));
    } catch (error) { out.replaceChildren(h("p", { class: "error", text: error.message })); }
  };

  const ask = () => previewThen("Send to the AI and ask", async () => {
    const r = await post("/api/ai/ask", body({ question: a.question, confirm: true }));
    out.replaceChildren(h("div", { class: "panel" }, h("h2", { text: "Answer" }), h("div", { class: "answer", text: r.answer }),
      r.cut_short ? h("div", { class: "notice", text: "The answer was cut off because it reached the length limit." }) : null,
      h("p", { class: "small muted", text: "Based on " + fmt(r.messages_sent) + " of " + fmt(r.total_matching) + " matching messages. Answered by " + r.model + ". AI answers can be wrong; check the messages themselves for anything important." })));
  });
  const findTopics = () => previewThen("Send to the AI and find topics", async () => {
    const r = await post("/api/ai/topics", body({ confirm: true }));
    out.replaceChildren(h("div", { class: "panel" }, h("h2", { text: "Suggested topics" }),
      h("p", { class: "small muted", text: "From " + fmt(r.messages_sent) + " of " + fmt(r.total_matching) + " matching messages. Nothing is saved until you press Save." }),
      r.topics.map((t) => {
        const button = h("button", { class: "quiet small", text: "Save as topic", onclick: async () => { await post("/api/topics/save", { name: t.name, terms: t.keywords }); button.textContent = "Saved"; button.disabled = true; } });
        return h("div", { class: "msg" }, h("div", { class: "row" }, h("strong", { text: t.name }), button), h("div", { text: t.description }),
          h("div", { class: "chips" }, t.keywords.map((k) => h("span", { class: "chip", text: k }))));
      })));
  });

  show(aiSetup(ai),
    h("div", { class: "panel" }, h("h2", { text: "Ask AI about your messages" }),
      definition("Uses the filters above plus the optional search below to choose messages. You always see exactly what would be sent, and nothing is sent until you confirm."),
      h("div", { class: "row grow" }, q, strategy), h("div", { class: "spacer" }),
      h("div", { class: "row" }, h("span", { class: "small muted", text: "Ready-made questions:" }),
        h("button", { class: "quiet small", text: "Holistic read of a relationship", onclick: () => { question.value = AI_PRESETS.holistic; strategy.value = "spread"; } }),
        h("button", { class: "quiet small", text: "Tone and emotional arc", onclick: () => { question.value = AI_PRESETS.tone; strategy.value = "spread"; } }),
        h("button", { class: "quiet small", text: "What we talk about", onclick: () => { question.value = AI_PRESETS.themes; strategy.value = "spread"; } })),
      h("div", { class: "spacer" }), question, h("div", { class: "spacer" }),
      h("div", { class: "row" }, h("button", { class: "primary", text: "Preview and ask", onclick: () => { sync(); if (!a.question.trim()) { out.replaceChildren(h("p", { class: "error", text: "Type a question first." })); return; } ask(); } }),
        h("button", { text: "Preview and find topics", onclick: findTopics }))),
    out);
}

/* The app itself: which version this is, getting a newer one, and quitting. */
function appPanel(version) {
  const out = h("div", {});
  const say = (...nodes) => out.replaceChildren(...nodes.flat(Infinity).filter(Boolean));
  const waitThenReload = async () => {
    // The app restarts itself on the new version. Reload this page once it answers again.
    for (let attempt = 0; attempt < 60; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      try { if ((await fetch("/", { cache: "no-store" })).ok) { location.reload(); return; } } catch (e) { /* not back yet */ }
    }
    say(h("p", { class: "error", text: "The new version was installed but did not start. Open iMessage Explorer again from Applications." }));
  };
  const install = async () => {
    say(h("p", { class: "muted", text: "Downloading and installing. This takes a few seconds…" }));
    try {
      const result = await post("/api/app/update", {});
      if (!result.updated_to) { say(h("p", { text: result.message })); return; }
      say(h("p", { text: "Version " + result.updated_to + " is installed. Restarting…" }));
      waitThenReload();
    } catch (error) { say(h("p", { class: "error", text: error.message })); }
  };
  const check = async () => {
    say(h("p", { class: "muted", text: "Checking…" }));
    try {
      const found = await request("/api/app/check");
      if (!found.latest) { say(h("p", { text: "No published version was found yet." })); return; }
      if (!found.update_available) { say(h("p", { text: "You have the newest version." })); return; }
      say(h("p", {}, h("strong", { text: "Version " + found.latest + " is available." })),
        found.can_update_itself
          ? h("button", { class: "primary", text: "Update the app", onclick: install })
          : h("p", { class: "small muted", text: "This copy was not put in place by the installer, so it cannot replace itself. Download the newest version and double-click the installer in it." }));
    } catch (error) { say(h("p", { class: "error", text: error.message })); }
  };
  const quit = async () => {
    try { await post("/api/quit", {}); } catch (e) { /* it may stop before answering */ }
    document.body.replaceChildren(h("main", {}, h("div", { class: "panel" }, h("h2", { text: "iMessage Explorer has stopped" }),
      h("p", { text: "You can close this tab and the Terminal window. Open it again from Applications whenever you like." }))));
  };
  return h("div", { class: "panel" }, h("h2", { text: "This app" }),
    h("p", { text: "You are running version " + version + "." }),
    h("div", { class: "row" }, h("button", { text: "Check for a newer version", onclick: check }), h("button", { class: "quiet", text: "Quit iMessage Explorer", onclick: quit })),
    out,
    h("p", { class: "small muted", text: "Checking asks GitHub which version is published. Nothing about you or your messages is sent. Updating the app never touches your messages, labels, pins or topics." }));
}

async function renderConnect() {
  const meta = await get("/api/meta", {});
  state.meta = meta;
  const command = meta.python + " " + meta.launcher + " mcp";
  const json = JSON.stringify({ mcpServers: { imessage: { command: meta.python, args: [meta.launcher, "mcp"] } } }, null, 2);
  const imp = meta.import;
  show(
    appPanel(meta.version),
    h("div", { class: "panel" }, h("h2", { text: "Let an AI tool search your messages" }),
      definition("AI tools on this Mac can search the local database through a standard connection called MCP. It is read-only and capped at 50 messages per request. Anything a tool retrieves is sent on to that tool's own AI service."),
      h("h3", { text: "Claude Code" }), h("pre", { class: "code", text: "claude mcp add imessage -- " + command }),
      h("h3", { text: "Claude Desktop or Cursor" }),
      h("p", { class: "small muted", text: "Add this to the tool's MCP settings file (Claude Desktop: Settings, Developer, Edit Config. Cursor: ~/.cursor/mcp.json), then restart the tool." }),
      h("pre", { class: "code", text: json }),
      h("p", { class: "small muted", text: "A bot that runs in the cloud cannot reach this database, because the database never leaves this Mac. Only tools running here can connect." })),
    h("div", { class: "panel" }, h("h2", { text: "What has been shared with AI" }),
      meta.activity.length ? h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "When" }), h("th", { text: "What" }), h("th", { class: "n", text: "Messages" }), h("th", { text: "Conversations" }))),
        h("tbody", {}, meta.activity.map((e) => h("tr", {}, h("td", { text: e.when }),
          h("td", { text: e.kind === "connected_tool" ? "Connected tool: " + e.tool + (e.query ? ' "' + e.query + '"' : "") : e.kind === "ask_ai" ? "Ask AI" : e.kind === "reading_pilot" ? "Reading pilot" : e.kind === "full_read" ? "Full read" : "Find topics" }),
          h("td", { class: "n", text: fmt(e.messages_sent !== undefined ? e.messages_sent : e.messages_returned) }),
          h("td", { text: (e.conversations || []).join(", ") }))))))
        : h("p", { class: "muted", text: "Nothing yet. Every AI request and connected-tool search will be listed here, without the message text." })),
    h("div", { class: "panel" }, h("h2", { text: "What this Mac records" }),
      definition("Some analysis depends on extra details that only newer versions of macOS keep, or that other people choose to share. This is what was found the last time your messages were brought in. No message text is involved."),
      imp.signals && imp.signals.length ? h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "Detail" }), h("th", { text: "Status" }), h("th", { class: "n", text: "Messages" }), h("th", { class: "n", text: "First seen" }))),
        h("tbody", {}, imp.signals.map((e) => h("tr", {}, h("td", { text: e.signal }),
          h("td", { text: !e.recorded ? "Not recorded on this Mac" : e.messages ? "Recorded" : "Recorded, none found" }),
          h("td", { class: "n", text: e.recorded ? fmt(e.messages) + " (" + e.share + "%)" : "–" }), h("td", { class: "n", text: e.first_seen || "–" }))))))
        : h("p", { class: "muted", text: "Press Update messages to check." })),
    h("div", { class: "panel" }, h("h2", { text: "Your local database" }),
      h("p", { text: "Stored in " + meta.data_folder + ". Last updated " + (imp.finished_at || "never") + "." }),
      imp.undecoded_bodies ? h("div", { class: "notice", text: fmt(imp.undecoded_bodies) + " messages had text this tool could not read. They are stored without their words." }) : null,
      h("p", { class: "small muted", text: fmt(imp.total_in_local_database) + " messages from " + imp.first_day + " to " + imp.last_day + ". " + imp.handles_with_contact_name + " of " + imp.handles_total + " numbers and emails matched a contact name. Press Update messages at the top right to bring in new ones." })));
}

/* ---------- navigation and filters ---------- */

/* Six sections. Each holds one or more screens. */
const NAV = [
  { id: "home", label: "Home", screens: [["home", "Home"]] },
  { id: "people", label: "People", screens: [["ranking", "Your people"], ["circles", "Circles"], ["profile", "Profile"], ["people", "All conversations"], ["hidden", "Hidden senders"]] },
  { id: "search", label: "Search", screens: [["search", "Search"], ["topics", "Topics"]] },
  { id: "patterns", label: "Patterns", screens: [["trends", "Over time"], ["replies", "Replies"], ["reactions", "Reactions"], ["tone", "Tone"], ["words", "Words"], ["expressions", "Expressions"], ["language", "We and I"], ["sharing", "Sharing"]] },
  { id: "reading", label: "Reading", screens: [["full", "Full read"], ["digests", "Digests"], ["pilot", "Pilot"], ["review", "Pilot review"]] },
  { id: "ask", label: "Ask AI", screens: [["ask", "Ask AI"]] },
  { id: "setup", label: "Setup", screens: [["connect", "Setup"]] },
];
/* Screens that always use your whole history, so the filter bar is hidden on them. */
const UNFILTERED = new Set(["home", "ranking", "circles", "profile", "connect", "pilot", "review", "hidden", "full", "digests"]);
const lastScreen = {};
const SCREENS = {};

function groupOf(screen) { return NAV.find((group) => group.screens.some(([id]) => id === screen)); }

function setTab(name) {
  if (name === "community") name = "ranking";
  const group = groupOf(name);
  state.tab = name;
  lastScreen[group.id] = name;
  for (const button of document.querySelectorAll("#nav button")) {
    if (button.dataset.tab === group.id) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  }
  const subnav = document.getElementById("subnav");
  subnav.replaceChildren(...(group.screens.length > 1 ? group.screens.map(([id, label]) => {
    const pill = h("button", { type: "button", "data-screen": id, text: label, onclick: () => setTab(id) });
    if (id === name) pill.setAttribute("aria-current", "page");
    return pill;
  }) : []));
  document.getElementById("filters").hidden = UNFILTERED.has(name);
  Promise.resolve(SCREENS[name]()).catch(failure);
}

const filterInputs = { contact: "f-contact", date_from: "f-from", date_to: "f-to", direction: "f-direction", scope: "f-scope" };
function syncFilterInputs() {
  for (const [key, id] of Object.entries(filterInputs)) document.getElementById(id).value = state.filters[key];
  document.getElementById("f-junk").checked = state.filters.include_junk === "1";
}
function setFilter(key, value) { state.filters[key] = value; state.search.offset = 0; syncFilterInputs(); setTab(state.tab); }

async function loadShell() {
  state.meta = await get("/api/meta", {});
  const t = state.meta.totals;
  document.getElementById("summary").textContent = fmt(t.messages) + " messages with " + fmt(t.people) + " people and " + fmt(t.groups) + " groups, " + t.first_day + " to " + t.last_day + ".";
  const contacts = await get("/api/contacts", {});
  document.getElementById("contact-list").replaceChildren(...contacts.contacts.map((c) => h("option", { value: c.name })));
}

/* Bring in new messages without leaving the browser. */
async function runUpdate() {
  const button = document.getElementById("update");
  button.disabled = true;
  button.textContent = "Updating…";
  show(h("div", { class: "panel" }, h("h2", { text: "Bringing in your messages" }),
    h("p", { text: "Reading Messages on this Mac and adding anything new. This takes roughly a minute per million messages. Keep this window open." })));
  try {
    const result = await post("/api/update", {});
    await loadShell();
    show(h("div", { class: "panel" }, h("h2", { text: "Update finished" }), h("pre", { class: "report", text: result.summary }),
      h("button", { class: "primary", text: "Continue", onclick: () => setTab("home") })));
  } catch (error) {
    show(h("div", { class: "panel" }, h("h2", { text: "The update did not finish" }), h("p", { class: "error", text: error.message }),
      h("p", { class: "muted small", text: "Nothing already in your local database was changed. You can try again." })));
  } finally {
    button.disabled = false;
    button.textContent = "Update messages";
  }
}

async function start() {
  document.getElementById("nav").replaceChildren(...NAV.map((group) =>
    h("button", { type: "button", "data-tab": group.id, text: group.label, onclick: () => setTab(lastScreen[group.id] || group.screens[0][0]) })));
  for (const [key, id] of Object.entries(filterInputs)) {
    document.getElementById(id).addEventListener("change", (event) => setFilter(key, event.target.value.trim()));
  }
  document.getElementById("f-junk").addEventListener("change", (event) => setFilter("include_junk", event.target.checked ? "1" : ""));
  document.getElementById("f-clear").addEventListener("click", () => {
    for (const key of Object.keys(state.filters)) state.filters[key] = "";
    state.search.offset = 0; syncFilterInputs(); setTab(state.tab);
  });
  document.getElementById("update").addEventListener("click", runUpdate);
  document.getElementById("filters").hidden = true;
  try {
    const status = await request("/api/status");
    if (!status.ready || !status.current) {
      document.getElementById("summary").textContent = "";
      show(h("div", { class: "panel start-here" },
        h("h2", { text: status.ready ? "One update needed" : "Welcome. Let's bring in your messages." }),
        h("p", { text: status.ready
          ? "This version adds new measurements. Updating re-reads your messages to add them. Nothing you already have is lost."
          : "This reads the Messages database on this Mac and builds a private, searchable copy that stays on this Mac." }),
        h("button", { class: "primary", text: status.ready ? "Update now" : "Bring in my messages", onclick: runUpdate })));
      return;
    }
    await loadShell();
    setTab("home");
  } catch (error) {
    document.getElementById("summary").textContent = "";
    failure(error);
  }
}


/* ======================================================================
 * Deeper analysis screens: Community, Profile, Tone, Expressions.
 * ====================================================================== */

const PART_LABELS = { volume: "Volume", consistency: "Consistency", recency: "Recency", two_sided: "Two-sided", warmth: "Warmth", depth: "Depth" };
const KIND_LABELS = { affection: "Affection", gratitude: "Gratitude", apology: "Apology", laughter: "Laughter", question: "Questions", prayer: "Prayer", encouragement: "Encouragement" };

function mini(value) {
  const fill = h("i", {});
  fill.style.width = Math.max(0, Math.min(100, value)) + "%";
  return h("span", { class: "mini", title: value + " out of 100" }, fill);
}
function signed(score) {
  if (score === null || score === undefined) return h("span", { class: "muted", text: "–" });
  return h("span", { class: score > 0 ? "pos" : score < 0 ? "neg" : "", text: (score > 0 ? "+" : "") + score });
}
function kv(pairs) {
  return h("dl", { class: "kv" }, pairs.filter(Boolean).map(([label, value]) => [h("dt", { text: label }), h("dd", {}, value)]));
}
function openProfile(name) { state.person = name; setTab("profile"); }
function personLink(name) { return h("button", { class: "link", text: name, onclick: () => openProfile(name) }); }
function change(now, before) {
  if (before === undefined || before === null) return "";
  const diff = Math.round((now - before) * 10) / 10;
  return diff === 0 ? "same as a year earlier" : (diff > 0 ? "up " : "down ") + Math.abs(diff) + " from a year earlier";
}

/* Share of positive messages rising above a centre line, negative falling below. */
function toneChart(buckets) {
  if (!buckets.length) return h("p", { class: "muted", text: "No written messages in this selection." });
  const W = 1000, H = 260, left = 44, bottom = 22, top = 8;
  const max = Math.max(5, ...buckets.map((b) => Math.max(b.positive_share, b.negative_share)));
  const usable = H - bottom - top;
  const upShare = Math.max(...buckets.map((b) => b.positive_share), 1), downShare = Math.max(...buckets.map((b) => b.negative_share), 1);
  const zero = top + usable * (upShare / (upShare + downShare));
  const scale = Math.min((zero - top) / upShare, (H - bottom - zero) / downShare);
  const step = (W - left) / buckets.length;
  const barWidth = Math.max(1, Math.min(step * 0.82, 46));
  const chart = svg("svg", { class: "chart", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Share of positive and negative messages over time" });
  chart.append(svg("line", { x1: left, x2: W, y1: zero, y2: zero }));
  const upLabel = svg("text", { x: left - 6, y: top + 10, "text-anchor": "end" }); upLabel.textContent = Math.round(upShare) + "%";
  const downLabel = svg("text", { x: left - 6, y: H - bottom, "text-anchor": "end" }); downLabel.textContent = Math.round(downShare) + "%";
  chart.append(upLabel, downLabel);
  const labelEvery = Math.max(1, Math.ceil(buckets.length / 12));
  buckets.forEach((b, i) => {
    const x = left + i * step + (step - barWidth) / 2;
    const tip = svg("title", {});
    tip.textContent = b.period + ": tone " + b.score + ", " + b.positive_share + "% positive, " + b.negative_share + "% negative, " + fmt(b.messages) + " messages";
    const group = svg("g", {});
    group.append(tip,
      svg("rect", { class: "good", x, y: zero - b.positive_share * scale, width: barWidth, height: b.positive_share * scale }),
      svg("rect", { class: "bad", x, y: zero, width: barWidth, height: b.negative_share * scale }));
    chart.append(group);
    if (i % labelEvery === 0) {
      const text = svg("text", { x: x + barWidth / 2, y: H - 6, "text-anchor": "middle" });
      text.textContent = b.period;
      chart.append(text);
    }
  });
  return h("div", {}, h("div", { class: "legend" },
    h("span", {}, h("i", { class: "swatch good" }), "Share of messages that read positive"),
    h("span", {}, h("i", { class: "swatch bad" }), "Share that read negative")), chart);
}

function periodPicker(onChange) {
  const picker = h("select", { "aria-label": "Period" }, ["day", "week", "month", "year"].map((b) => h("option", { value: b, text: "By " + b })));
  picker.value = state.bucket;
  picker.addEventListener("change", () => { state.bucket = picker.value; onChange(); });
  return picker;
}

async function renderTone() {
  loading();
  const [people, time, moments] = await Promise.all([get("/api/tone/people"), get("/api/tone/time", { bucket: state.bucket }), get("/api/tone/moments")]);
  const o = people.overall;
  const order = state.toneOrder || "warmest";
  const sorted = people.rows.slice().sort((a, b) => order === "warmest" ? b.score - a.score : a.score - b.score);
  const sorter = h("select", { "aria-label": "Order" }, h("option", { value: "warmest", text: "Warmest first" }), h("option", { value: "tense", text: "Most tense first" }));
  sorter.value = order;
  sorter.addEventListener("change", () => { state.toneOrder = sorter.value; renderTone(); });
  const dayList = (items, withShare) => items.length ? bars(items.map((d) => ({ name: d.day, total: withShare ? d.negative_share : d.score, label: withShare ? d.negative_share + "% negative of " + d.messages : "tone +" + d.score + " over " + d.messages })),
    (r) => { state.filters.date_from = r.name; state.filters.date_to = r.name; syncFilterInputs(); state.search.q = ""; setTab("search"); }) : h("p", { class: "muted", text: "None found in this selection." });
  const messageList = (items) => items.length ? h("div", {}, items.map((m) => messageNode(m, [], { withContext: true }))) : h("p", { class: "muted", text: "None found in this selection." });
  show(
    h("div", { class: "cards" }, card((o.score > 0 ? "+" : "") + (o.score === null ? "–" : o.score), "overall tone, -100 to +100"), card((o.my_score > 0 ? "+" : "") + (o.my_score === null ? "–" : o.my_score), "my tone"),
      card((o.their_score > 0 ? "+" : "") + (o.their_score === null ? "–" : o.their_score), "their tone"), card(o.positive_share + "%", "messages that read positive"), card(o.negative_share + "%", "messages that read negative")),
    h("div", { class: "panel" }, h("div", { class: "row" }, h("h2", { text: "Tone over time" }), periodPicker(renderTone)), definition(people.definition), toneChart(time.buckets)),
    h("div", { class: "panel" }, h("div", { class: "row" }, h("h2", { text: "Tone by person" }), sorter),
      sorted.length ? h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "Person or group" }), h("th", { class: "n", text: "Tone" }), h("th", { class: "n", text: "Mine" }), h("th", { class: "n", text: "Theirs" }),
          h("th", { class: "n", text: "Positive" }), h("th", { class: "n", text: "Negative" }), h("th", { class: "n", text: "Written messages" }))),
        h("tbody", {}, sorted.map((r) => h("tr", {}, h("td", {}, personLink(r.name), r.is_group ? " (group)" : ""), h("td", { class: "n" }, signed(r.score)), h("td", { class: "n" }, signed(r.my_score)),
          h("td", { class: "n" }, signed(r.their_score)), h("td", { class: "n", text: r.positive_share + "%" }), h("td", { class: "n", text: r.negative_share + "%" }), h("td", { class: "n", text: fmt(r.messages) }))))))
        : h("p", { class: "muted", text: "Nobody has enough written messages in this selection to rank." })),
    h("div", { class: "panel" }, h("h2", { text: "Standout moments" }), definition(moments.definition),
      h("div", { class: "pair" }, h("div", {}, h("h3", { text: "Warmest days" }), dayList(moments.best_days, false)), h("div", {}, h("h3", { text: "Most tense days" }), dayList(moments.worst_days, true)))),
    h("div", { class: "pair" },
      h("div", { class: "panel" }, h("h2", { text: "Most positive messages" }), messageList(moments.most_positive)),
      h("div", { class: "panel" }, h("h2", { text: "Most negative messages" }), messageList(moments.most_negative))),
    h("div", { class: "panel" }, h("p", { class: "small muted", text: "For a careful read of one relationship or period, set the filters above and use the tone question on the Ask AI screen. The AI reads meaning; this screen only reads words." }),
      h("button", { text: "Ask AI about the tone of this selection", onclick: () => { state.ask.question = AI_PRESETS.tone; setTab("ask"); } })));
}

const LABEL_NAMES = { "": "No label", "family": "Family", "close friend": "Close friend", "friend": "Friend", "work": "Work", "service": "Service" };

/* A label menu and a pin button for one person. */
function labelControls(person, afterSave) {
  const menu = h("select", { "aria-label": "Label for " + person.name }, Object.entries(LABEL_NAMES).map(([value, text]) => h("option", { value, text })));
  menu.value = person.label || "";
  const pin = h("button", { type: "button", class: person.pinned ? "on" : "", text: person.pinned ? "Pinned" : "Pin", title: "Pinned people are your inner circle and are always listed first" });
  const save = async (label, pinned) => {
    try { await post("/api/labels/save", { name: person.name, label, pinned }); afterSave(); }
    catch (error) { alert(error.message); }
  };
  menu.addEventListener("change", () => save(menu.value, person.pinned));
  pin.addEventListener("click", () => save(menu.value, !person.pinned));
  return h("span", { class: "inline-controls" }, menu, pin);
}

function personPicker() {
  const input = h("input", { list: "contact-list", placeholder: "Type a name to open their profile", autocomplete: "off", "aria-label": "Open a profile" });
  const open = () => { if (input.value.trim()) openProfile(input.value.trim()); };
  input.addEventListener("change", open);
  return h("span", { class: "inline-controls" }, input, h("button", { type: "button", text: "Open", onclick: open }));
}

async function renderHome() {
  loading();
  const d = await get("/api/home", {});
  const t = d.totals, now = d.community.now;
  const pinNow = async (name, label) => { await post("/api/labels/save", { name, label: label || "", pinned: true }); renderHome(); };
  const circle = d.inner_circle.length
    ? h("div", { class: "panel" }, h("h2", { text: "Your inner circle" }),
        h("div", { class: "person-cards" }, d.inner_circle.map((r) => h("div", { class: "person-card" },
          h("div", { class: "who" }, personLink(r.name), r.label ? h("span", { class: "tag", text: LABEL_NAMES[r.label] }) : null),
          h("div", { class: "muted small", text: fmt(r.messages) + " messages over " + r.years_known + " years" }),
          h("div", { class: "muted small", text: r.days_since_last === 0 ? "In touch at the end of your history" : "Last message " + r.last_day })))),
        h("div", { class: "spacer" }), h("button", { class: "quiet", text: "Change who is pinned", onclick: () => setTab("ranking") }))
    : h("div", { class: "panel start-here" }, h("h2", { text: "Start here: pin your inner circle" }),
        h("p", { text: "Messages cannot show who you live with or see every day, so the people closest to you often text you the least. Pin them and they are always listed first. These are the people your messages suggest. Pin the ones who belong, and add anyone missing from Your people." }),
        h("div", { class: "people-pick" }, d.suggested.map((r) => h("button", { type: "button", text: "Pin " + r.name, onclick: () => pinNow(r.name, r.label) }))),
        h("div", { class: "spacer" }), h("button", { class: "quiet", text: "See everyone", onclick: () => setTab("ranking") }));
  const list = (items, text) => items.length ? h("div", {}, items.map((i) => h("div", {}, personLink(i.name), h("span", { class: "muted small", text: "  " + text(i) })))) : h("p", { class: "muted small", text: "Nobody right now." });
  show(
    h("div", { class: "cards" }, card(fmt(t.messages), "messages"), card(fmt(d.people_scored), "people you write to"), card(fmt(now.steady_contacts), "steady contacts this year"),
      card(fmt(now.active_last_30_days), "people in the last 30 days"), card(t.first_day + " to " + t.last_day, "your history")),
    circle,
    h("div", { class: "panel" }, h("h2", { text: "What has changed lately" }),
      h("div", { class: "grid3" },
        h("div", {}, h("h3", { text: "Talking more" }), list(d.drift.growing, (i) => fmt(i.before) + " then " + fmt(i.recent) + " messages")),
        h("div", {}, h("h3", { text: "Talking less" }), list(d.drift.fading, (i) => fmt(i.before) + " then " + fmt(i.recent) + " messages")),
        h("div", {}, h("h3", { text: "Gone quiet" }), list(d.drift.gone_quiet, (i) => "silent " + fmt(i.days_silent) + " days after " + fmt(i.total) + " messages"))),
      h("p", { class: "muted small", text: "Compares the last 180 days of your history with the 180 days before." })),
    d.hidden.senders || d.hidden.suggested ? h("p", { class: "muted small" },
      fmt(d.hidden.messages) + " messages from " + fmt(d.hidden.senders) + " automated senders (codes, alerts, marketing) are set aside" +
      (d.hidden.suggested ? ", and " + fmt(d.hidden.suggested) + " more senders look like junk. " : ". "),
      h("button", { class: "link", text: "Review hidden senders", onclick: () => setTab("hidden") })) : null,
    h("div", { class: "panel" }, h("h2", { text: "Where to go" }),
      h("div", { class: "row" },
        h("button", { text: "Find a message", onclick: () => setTab("search") }), h("button", { text: "See your people", onclick: () => setTab("ranking") }),
        h("button", { text: "Open someone's profile", onclick: () => { state.person = ""; setTab("profile"); } }), h("button", { text: "Explore patterns", onclick: () => setTab("trends") }),
        h("button", { text: "Have AI read your conversations", onclick: () => setTab("full") }),
        h("button", { text: "Ask AI a question", onclick: () => setTab("ask") })),
      h("p", { class: "muted small", text: "Last updated " + (d.import.finished_at || "never") + ". Press Update messages at the top right to bring in new ones." })));
}

async function renderRanking() {
  loading();
  const [ranked, c] = await Promise.all([get("/api/importance", { contact: "", direction: "", scope: "", date_from: "", date_to: "" }), get("/api/community", {})]);
  const only = state.rankLabel || "";
  const menu = h("select", { "aria-label": "Show" }, h("option", { value: "", text: "Everyone" }), h("option", { value: "pinned", text: "Inner circle only" }),
    Object.entries(LABEL_NAMES).filter(([value]) => value && value !== "service").map(([value, text]) => h("option", { value, text: text + " only" })), h("option", { value: "none", text: "Not labelled yet" }));
  menu.value = only;
  menu.addEventListener("change", () => { state.rankLabel = menu.value; renderRanking(); });
  const rows = ranked.rows.filter((r) => !only || (only === "pinned" ? r.pinned : only === "none" ? !r.label && !r.pinned : r.label === only));
  const drift = c.drift;
  const driftList = (items, label) => items.length ? h("div", {}, items.map((i) => h("div", {}, personLink(i.name), h("span", { class: "muted small", text: "  " + label(i) })))) : h("p", { class: "muted small", text: "Nobody right now." });
  show(
    h("div", { class: "panel" }, h("div", { class: "row" }, h("h2", { text: "Your people" }), menu), definition(ranked.definition),
      h("p", { class: "small muted", text: "Use the menu on each row to say who someone is, and Pin for your inner circle. Label a business or delivery number as Service to remove it from this list. " + fmt(ranked.people_scored) + " people are listed." }),
      rows.length ? h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { class: "n", text: "#" }), h("th", { text: "Person" }), h("th", { text: "Label and pin" }), h("th", { class: "n", text: "Score" }),
          Object.values(PART_LABELS).map((label) => h("th", { class: "n", text: label })), h("th", { class: "n", text: "Messages" }), h("th", { class: "n", text: "Last message" }))),
        h("tbody", {}, rows.slice(0, 150).map((r) => h("tr", {}, h("td", { class: "n", text: r.rank }), h("td", {}, personLink(r.name)),
          h("td", {}, labelControls(r, renderRanking)), h("td", { class: "n" }, h("strong", { text: r.score })),
          Object.keys(PART_LABELS).map((key) => h("td", { class: "n" }, String(r.parts[key]), mini(r.parts[key]))),
          h("td", { class: "n", text: fmt(r.messages) }), h("td", { class: "n", text: r.last_day }))))))
        : h("p", { class: "muted", text: "Nobody matches that choice yet." })),
    h("div", { class: "panel" }, h("h2", { text: "Drifting and growing" }), definition(drift.definition),
      h("div", { class: "grid3" },
        h("div", {}, h("h3", { text: "Growing" }), driftList(drift.growing, (i) => fmt(i.before) + " then " + fmt(i.recent))),
        h("div", {}, h("h3", { text: "Fading" }), driftList(drift.fading, (i) => fmt(i.before) + " then " + fmt(i.recent))),
        h("div", {}, h("h3", { text: "Gone quiet" }), driftList(drift.gone_quiet, (i) => fmt(i.total) + " messages, silent " + fmt(i.days_silent) + " days")))));
}

async function renderCircles() {
  loading();
  const [rings, c] = await Promise.all([get("/api/circles", {}), get("/api/community", {})]);
  const now = c.now, was = c.year_earlier;
  show(
    h("div", { class: "panel" }, h("h2", { text: "Your circles" }), definition(rings.definition),
      rings.rings.map((ring) => h("div", { class: "ring" },
        h("h3", { text: ring.name + ": " + ring.people.length + " of " + ring.holds }),
        ring.people.length ? h("div", { class: "chips" }, ring.people.map((person) => h("span", { class: "chip" }, personLink(person.name), person.pinned ? " (pinned)" : "")))
          : h("p", { class: "muted small", text: "Empty. You have fewer people than this ring holds." }))),
      rings.beyond ? h("p", { class: "muted small", text: fmt(rings.beyond) + " more people fall outside the widest ring." }) : null),
    h("div", { class: "cards" },
      h("div", { class: "card" }, h("div", { class: "num", text: fmt(now.steady_contacts) }), h("div", { class: "lbl", text: "steady contacts" }), h("div", { class: "lbl", text: change(now.steady_contacts, was.steady_contacts) })),
      h("div", { class: "card" }, h("div", { class: "num", text: fmt(now.two_way_steady) }), h("div", { class: "lbl", text: "of those are two-way" }), h("div", { class: "lbl", text: change(now.two_way_steady, was.two_way_steady) })),
      h("div", { class: "card" }, h("div", { class: "num", text: now.breadth }), h("div", { class: "lbl", text: "breadth, in people" }), h("div", { class: "lbl", text: change(now.breadth, was.breadth) })),
      h("div", { class: "card" }, h("div", { class: "num", text: now.top5_share + "%" }), h("div", { class: "lbl", text: "of messages with top 5 people" }), h("div", { class: "lbl", text: change(now.top5_share, was.top5_share) })),
      card(fmt(now.active_last_30_days), "people in the last 30 days"), card(fmt(now.active_groups), "group chats this year")),
    h("div", { class: "panel" }, h("h2", { text: "Strength of your community" }), definition(c.definition),
      h("p", { class: "small muted", text: "As of " + c.as_of + "." }),
      h("h3", { text: "People you talk to regularly, by year" }), columns(c.by_year.map((y) => ({ period: y.year, sent: y.regular_people, received: 0 })), { single: "regular people" }),
      h("p", { class: "small muted", text: c.by_year_definition + " New people by year: " + c.by_year.map((y) => y.year + " " + y.new_people).join(", ") + "." })));
}

const REACTION_LABELS = { love: "Love", like: "Like", dislike: "Dislike", laugh: "Laugh", emphasis: "Emphasis", question: "Question", other: "Other" };

async function renderReactions() {
  loading();
  const d = await get("/api/reactions");
  if (!d.total) {
    show(h("div", { class: "panel" }, h("h2", { text: "Reactions" }), definition(d.definition), h("p", { class: "muted", text: "No reactions found in this selection." })));
    return;
  }
  const per = (value) => value === null || value === undefined ? "–" : value;
  show(
    h("div", { class: "cards" }, card(fmt(d.given), "reactions I gave"), card(fmt(d.received), "reactions I received"), d.unmatched ? card(fmt(d.unmatched), "could not be matched to a message") : null),
    h("div", { class: "panel" }, h("h2", { text: "Reactions by kind" }), definition(d.definition),
      h("div", { class: "legend" }, h("span", {}, h("i", { class: "swatch sent" }), "I gave"), h("span", {}, h("i", { class: "swatch received" }), "I received")),
      bars(d.by_type.map((k) => ({ name: REACTION_LABELS[k.kind] || k.kind, sent: k.given, received: k.received, label: fmt(k.given) + " given · " + fmt(k.received) + " received" })))),
    h("div", { class: "panel" }, h("h2", { text: "Who reacts to whom" }),
      h("p", { class: "small muted", text: "'Per 100' is how many reactions a person gives for every 100 messages the other writes." }),
      h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "Person or group" }), h("th", { class: "n", text: "I gave" }), h("th", { class: "n", text: "Per 100 of theirs" }), h("th", { class: "n", text: "They gave" }),
          h("th", { class: "n", text: "Per 100 of mine" }), h("th", { class: "n", text: "My laughs at them" }), h("th", { class: "n", text: "Their laughs at me" }))),
        h("tbody", {}, d.by_person.map((r) => h("tr", {}, h("td", {}, personLink(r.name), r.is_group ? " (group)" : ""), h("td", { class: "n", text: fmt(r.given) }),
          h("td", { class: "n", text: per(r.given_per_100) }), h("td", { class: "n", text: fmt(r.received) }),
          h("td", { class: "n", text: per(r.received_per_100) }), h("td", { class: "n", text: fmt(r.laughs_given) }), h("td", { class: "n", text: fmt(r.laughs_received) }))))))),
    h("div", { class: "panel" }, h("h2", { text: "My messages that got the most reactions" }),
      d.most_reacted_to.length ? h("div", {}, d.most_reacted_to.map((m) => h("div", {}, h("div", { class: "muted small", text: fmt(m.reactions) + (m.reactions === 1 ? " reaction" : " reactions") }), messageNode(m, [], { withContext: true }))))
        : h("p", { class: "muted", text: "None of my messages in this selection were reacted to." })));
}

async function renderLanguage() {
  loading("Reading how each of you writes…");
  const d = await get("/api/language");
  const order = state.languageOrder || "words";
  const menu = h("select", { "aria-label": "Order" }, h("option", { value: "words", text: "Most words first" }), h("option", { value: "matching", text: "Closest style match first" }),
    h("option", { value: "we", text: "My highest share of 'we' first" }));
  menu.value = order;
  menu.addEventListener("change", () => { state.languageOrder = menu.value; renderLanguage(); });
  const key = { words: (r) => r.my_words + r.their_words, matching: (r) => r.style_matching === null ? -1 : r.style_matching, we: (r) => r.my_we_share === null ? -1 : r.my_we_share }[order];
  const rows = d.rows.slice().sort((a, b) => key(b) - key(a));
  const share = (value) => value === null || value === undefined ? "–" : value + "%";
  const match = (value) => value === null || value === undefined ? "–" : value.toFixed(2);
  show(
    h("div", { class: "cards" }, card(share(d.overall.my_we_share), "my share of 'we' overall"), card(share(d.overall.their_we_share), "their share of 'we' overall")),
    h("div", { class: "panel" }, h("h2", { text: "'We' against 'I'" }), definition(d.we_talk_definition),
      h("h3", { text: "By year" }), h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "Year" }), h("th", { class: "n", text: "My share of 'we'" }), h("th", { class: "n", text: "Their share of 'we'" }), h("th", { class: "n", text: "Style matching" }))),
        h("tbody", {}, d.by_year.map((y) => h("tr", {}, h("td", { text: y.period }), h("td", { class: "n", text: share(y.my_we_share) }), h("td", { class: "n", text: share(y.their_we_share) }), h("td", { class: "n", text: match(y.style_matching) }))))))),
    h("div", { class: "panel" }, h("div", { class: "row" }, h("h2", { text: "By person" }), menu), definition(d.style_definition),
      rows.length ? h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "Person" }), h("th", { class: "n", text: "My share of 'we'" }), h("th", { class: "n", text: "Their share of 'we'" }), h("th", { class: "n", text: "Style matching" }),
          h("th", { class: "n", text: "My words" }), h("th", { class: "n", text: "Their words" }))),
        h("tbody", {}, rows.map((r) => h("tr", {}, h("td", {}, personLink(r.name)), h("td", { class: "n", text: share(r.my_we_share) }), h("td", { class: "n", text: share(r.their_we_share) }),
          h("td", { class: "n", text: match(r.style_matching) }), h("td", { class: "n", text: fmt(r.my_words) }), h("td", { class: "n", text: fmt(r.their_words) }))))))
        : h("p", { class: "muted", text: "No one-to-one chats in this selection." })));
}

const SHARE_LABELS = { photos: "Photos", videos: "Videos", audio: "Audio files", files: "Other files", voice_messages: "Voice messages", links: "Links" };

async function renderSharing() {
  loading();
  const d = await get("/api/sharing");
  const kind = state.shareKind || "photos";
  const menu = h("select", { "aria-label": "Kind" }, Object.entries(SHARE_LABELS).map(([value, text]) => h("option", { value, text })));
  menu.value = kind;
  menu.addEventListener("change", () => { state.shareKind = menu.value; renderSharing(); });
  const people = d.by_person.filter((r) => r[kind].me + r[kind].them > 0).sort((a, b) => (b[kind].me + b[kind].them) - (a[kind].me + a[kind].them)).slice(0, 25);
  show(
    h("div", { class: "panel" }, h("h2", { text: "What gets shared" }), definition(d.definition), legend(),
      bars(Object.keys(SHARE_LABELS).map((k) => ({ name: SHARE_LABELS[k], sent: d.totals[k].me, received: d.totals[k].them, label: fmt(d.totals[k].me) + " me · " + fmt(d.totals[k].them) + " them" })))),
    h("div", { class: "panel" }, h("div", { class: "row" }, h("h2", { text: "One kind in detail" }), menu),
      h("h3", { text: SHARE_LABELS[kind] + " by year" }), columns(d.by_year.map((y) => ({ period: y.period, sent: y[kind].me, received: y[kind].them }))),
      h("h3", { text: SHARE_LABELS[kind] + " by person" }), people.length ? h("div", {}, legend(), bars(people.map((r) => ({ name: r.name, sent: r[kind].me, received: r[kind].them })), (r) => openProfile(r.name))) : h("p", { class: "muted", text: "None found in this selection." })),
    h("div", { class: "panel" }, h("h2", { text: "Websites linked most" }),
      d.websites.length ? h("div", {}, legend(), bars(d.websites.slice(0, 30).map((w) => ({ name: w.site, sent: w.me, received: w.them })), (r) => { state.search.q = r.name; state.search.offset = 0; setTab("search"); }))
        : h("p", { class: "muted", text: "No links found in this selection." })));
}

function termList(items, clickable) {
  if (!items || !items.length) return h("p", { class: "muted small", text: "Nothing distinctive enough to list." });
  return bars(items.map((i) => ({ name: i.term, total: i.count, label: fmt(i.count) + (i.times_usual ? " · " + i.times_usual + "x usual" : "") })),
    clickable ? (r) => { state.search.q = r.name.includes(" ") ? '"' + r.name + '"' : r.name; state.search.offset = 0; setTab("search"); } : null);
}

async function renderProfile() {
  const name = state.person;
  if (!name) {
    loading();
    const ranked = await get("/api/importance", { direction: "", scope: "" });
    show(h("div", { class: "panel" }, h("h2", { text: "Choose a person" }),
      h("p", { class: "muted", text: "A profile gathers everything about one relationship on one page. Pick someone below, or type a name." }),
      personPicker(), h("div", { class: "spacer" }),
      h("div", { class: "people-pick" }, ranked.rows.slice(0, 40).map((r) => h("button", { text: r.name, onclick: () => openProfile(r.name) })))));
    return;
  }
  loading("Building the profile…");
  const p = await request("/api/profile?" + new URLSearchParams({ contact: name }).toString());
  const t = p.totals, s = p.streaks, imp = p.importance, bal = p.balance, rep = p.replies;
  const sections = [];
  // These two take longest to work out, so the page appears first and they fill in after.
  const phrasesHolder = h("div", {}, h("p", { class: "muted small", text: "Counting…" }));
  const signatureHolder = h("div", {}, h("p", { class: "muted small", text: "Comparing with how everyone else writes…" }));
  const emojiHolder = h("div", {}, h("p", { class: "muted small", text: "Counting…" }));
  const languageHolder = h("div", {}, h("p", { class: "muted small", text: p.is_group ? "Measured for one-to-one chats only." : "Counting…" }));
  sections.push(h("div", { class: "cards" }, card(fmt(t.messages), "messages"), card(fmt(t.sent), "sent by me"), card(fmt(t.received), "received"),
    card(s.years_since_first + " years", "since the first message"), card(fmt(t.active_days), "days with messages"),
    p.tone ? card((p.tone.score > 0 ? "+" : "") + p.tone.score, "tone, -100 to +100") : null));

  if (imp) {
    sections.push(h("div", { class: "panel" }, h("h2", { text: "Importance" }), definition(p.importance_definition),
      h("div", { class: "row" }, h("div", { class: "big-score", text: imp.score }), h("div", {}, h("div", { text: "out of 100" }), h("div", { class: "muted small", text: "Ranked " + imp.rank + " of " + p.people_scored + " people" }))),
      h("div", { class: "spacer" }),
      bars(Object.keys(PART_LABELS).map((key) => ({ name: PART_LABELS[key], total: imp.parts[key], label: imp.parts[key] + " of 100" })), null, 100)));
  } else if (!p.is_group) {
    sections.push(h("div", { class: "panel" }, h("h2", { text: "Importance" }), h("p", { class: "muted", text: "Not enough one-to-one messages with this person to score yet." })));
  }

  sections.push(h("div", { class: "pair" },
    h("div", { class: "panel" }, h("h2", { text: "Messages by year" }), columns(p.by_year)),
    h("div", { class: "panel" }, h("h2", { text: "Tone by year" }), p.tone_by_year.length ? toneChart(p.tone_by_year) : h("p", { class: "muted", text: "No written messages." }))));

  sections.push(h("div", { class: "pair" },
    h("div", { class: "panel" }, h("h2", { text: "History" }), kv([
      ["First message", s.first_day], ["Most recent", s.last_day],
      ["Longest streak", s.longest_streak.days + " days in a row (" + s.longest_streak.from + " to " + s.longest_streak.to + ")"],
      ["Current streak", s.current_streak_days ? s.current_streak_days + " days" : "none"],
      ["Biggest day", s.biggest_day.day + ", " + fmt(s.biggest_day.messages) + " messages"],
      ["Milestones", s.milestones.map((m) => fmt(m.count) + (m.count === 1 ? "st" : "th") + " message on " + m.day).join("; ")]]),
      s.first_message ? h("div", {}, h("div", { class: "spacer" }), h("h3", { text: "The first message" }), messageNode(s.first_message, [], { withContext: true })) : null),
    h("div", { class: "panel" }, h("h2", { text: "How you talk" }),
      rep ? kv([["My typical reply", duration(rep.my_median_seconds)], ["Their typical reply", duration(rep.their_median_seconds)],
        ["Conversations I start", fmt(rep.started_by_me) + " of " + fmt(rep.conversations)], ["Longest silence", rep.longest_gap_days + " days"]]) : null,
      bal ? h("div", {}, h("div", { class: "spacer" }), kv([
        ["Words written", "me " + fmt(bal.my_words) + ", them " + fmt(bal.their_words) + (bal.my_word_share !== null ? " (I write " + bal.my_word_share + "%)" : "")],
        ["Average message", "me " + bal.my_average_words + " words, them " + bal.their_average_words + " words"],
        ["Three or more in a row", "me " + fmt(bal.my_runs) + " times, them " + fmt(bal.their_runs) + " times"],
        ["Questions asked", "me " + fmt(bal.my_questions) + ", them " + fmt(bal.their_questions)],
        ["Questions left unanswered", "by me " + fmt(bal.unanswered_by_me) + ", by them " + fmt(bal.unanswered_by_them)]])) : null,
      !rep && !bal ? h("p", { class: "muted", text: "Reply and balance measures cover one-to-one chats only." }) : null)));

  sections.push(h("div", { class: "pair" },
    h("div", { class: "panel" }, h("h2", { text: "Expressions" }), legend(),
      bars(Object.keys(KIND_LABELS).map((k) => ({ name: KIND_LABELS[k], sent: p.expressions[k].me, received: p.expressions[k].them, label: fmt(p.expressions[k].me) + " me · " + fmt(p.expressions[k].them) + " them" })))),
    h("div", { class: "panel" }, h("h2", { text: "Emoji and repeated phrases" }), h("h3", { text: "Top emoji" }), emojiHolder,
      h("div", { class: "spacer" }), h("h3", { text: "Repeated phrases" }), phrasesHolder)));

  const rx = p.reactions, rt = p.read_times, sh = p.sharing, re2 = p.replies_and_edits;
  const both = (pair) => "me " + fmt(pair.me) + ", them " + fmt(pair.them);
  sections.push(h("div", { class: "pair" },
    h("div", { class: "panel" }, h("h2", { text: "Reactions, reading and sharing" }), kv([
      ["Reactions", rx.total ? "I gave " + fmt(rx.given) + ", they gave " + fmt(rx.received) : "none recorded"],
      rt.me_reading_them.messages ? ["I read their messages in", duration(rt.me_reading_them.median_seconds) + " typically (" + fmt(rt.me_reading_them.messages) + " recorded)"] : null,
      rt.them_reading_me.messages ? ["They read mine in", duration(rt.them_reading_me.median_seconds) + " typically (" + fmt(rt.them_reading_me.messages) + " recorded)"] : ["They read mine in", "not shared with you"],
      ["Photos", both(sh.photos)], ["Videos", both(sh.videos)], ["Voice messages", both(sh.voice_messages)], ["Links", both(sh.links)],
      ["Direct replies", both(re2.direct_replies)], ["Edited", both(re2.edited)], ["Unsent", both(re2.unsent)]])),
    h("div", { class: "panel" }, h("h2", { text: "We and I" }),
      languageHolder,
      h("div", { class: "spacer" }), definition(p.we_talk_definition), definition(p.style_definition))));

  if (!p.is_group) {
    sections.push(h("div", { class: "panel" }, h("h2", { text: "Signature words and phrases" }), definition(p.signature_definition), signatureHolder));
  }

  sections.push(h("div", { class: "panel" }, h("h2", { text: "A fuller read" }),
    h("p", { class: "small muted", text: "Everything above is counted from message patterns. It cannot tell you what this person means to you. For a written, holistic read of the relationship and its place in your life, the AI can read a spread of your messages with " + name + ". You will see exactly what would be sent before anything leaves your Mac." }),
    h("button", { class: "primary", text: "Ask AI for a holistic read", onclick: () => { state.ask.q = ""; state.ask.strategy = "spread"; state.ask.question = AI_PRESETS.holistic; setTab("ask"); } })));
  show(h("div", { class: "panel" },
    h("div", { class: "row" }, h("h2", { text: name + (p.is_group ? " (group chat)" : "") }), p.is_group ? null : labelControls({ name, label: p.label, pinned: p.pinned }, renderProfile)),
    h("div", { class: "row" }, personPicker(),
      h("button", { class: "quiet", text: "See the messages", onclick: () => { state.filters.contact = name; syncFilterInputs(); state.search.q = ""; state.search.offset = 0; setTab("search"); } }))),
    sections);
  const stillHere = () => state.tab === "profile" && state.person === name;
  request("/api/phrases?" + new URLSearchParams({ contact: name }).toString()).then((found) => {
    if (!stillHere()) return;
    const all = [].concat(found.by_length["6+"] || [], found.by_length["5"] || [], found.by_length["4"] || [], found.by_length["3"] || []).sort((a, b) => b.count - a.count).slice(0, 12);
    phrasesHolder.replaceChildren(termList(all, true));
  }).catch((error) => phrasesHolder.replaceChildren(h("p", { class: "error", text: error.message })));
  request("/api/stats/words?" + new URLSearchParams({ contact: name }).toString()).then((found) => {
    if (stillHere()) emojiHolder.replaceChildren(termList(found.emoji.slice(0, 12), false));
  }).catch((error) => emojiHolder.replaceChildren(h("p", { class: "error", text: error.message })));
  if (!p.is_group) {
    request("/api/language?" + new URLSearchParams({ contact: name }).toString()).then((found) => {
      if (!stillHere()) return;
      const lang = found.rows.find((row) => row.name === name);
      languageHolder.replaceChildren(lang ? kv([
        ["My share of 'we'", lang.my_we_share === null ? "not enough words" : lang.my_we_share + "%"],
        ["Their share of 'we'", lang.their_we_share === null ? "not enough words" : lang.their_we_share + "%"],
        ["Style matching", lang.style_matching === null ? "not enough words" : lang.style_matching.toFixed(2) + " out of 1.00"]]) : h("p", { class: "muted", text: "Not enough written messages to measure." }));
    }).catch((error) => languageHolder.replaceChildren(h("p", { class: "error", text: error.message })));
    request("/api/signature?" + new URLSearchParams({ contact: name }).toString()).then((sig) => {
      if (!stillHere()) return;
      signatureHolder.replaceChildren(h("div", { class: "pair" },
        h("div", {}, h("h3", { text: "What " + name + " says more than others" }), termList(sig.theirs.phrases.slice(0, 10), true), h("div", { class: "spacer" }), termList(sig.theirs.words.slice(0, 12), true)),
        h("div", {}, h("h3", { text: "What I say more with " + name }), termList(sig.mine.phrases.slice(0, 10), true), h("div", { class: "spacer" }), termList(sig.mine.words.slice(0, 12), true))));
    }).catch((error) => signatureHolder.replaceChildren(h("p", { class: "error", text: error.message })));
  }
}

async function renderExpressions() {
  loading();
  const [e, st] = await Promise.all([get("/api/expressions", { bucket: state.bucket === "day" || state.bucket === "week" ? "month" : state.bucket }), get("/api/style")]);
  const kind = state.expressionKind || "affection";
  const picker = h("select", { "aria-label": "Kind of expression" }, e.kinds.map((k) => h("option", { value: k.name, text: KIND_LABELS[k.name] })));
  picker.value = kind;
  picker.addEventListener("change", () => { state.expressionKind = picker.value; renderExpressions(); });
  const rate = (value) => value + "%";
  const styleCells = (row) => row ? [h("td", { class: "n", text: row.average_words }), h("td", { class: "n", text: rate(row.long_message_rate) }), h("td", { class: "n", text: rate(row.emoji_rate) }),
    h("td", { class: "n", text: rate(row.exclamation_rate) }), h("td", { class: "n", text: rate(row.question_rate) }), h("td", { class: "n", text: rate(row.all_caps_rate) }), h("td", { class: "n", text: rate(row.lowercase_start_rate) })] : Array.from({ length: 7 }, () => h("td", { class: "n", text: "–" }));
  const people = e.by_person.filter((r) => r[kind].me + r[kind].them > 0).sort((a, b) => (b[kind].me + b[kind].them) - (a[kind].me + a[kind].them)).slice(0, 25);
  show(
    h("div", { class: "panel" }, h("h2", { text: "Expressions" }), definition(e.definition), legend(),
      bars(e.kinds.map((k) => ({ name: KIND_LABELS[k.name], sent: e.totals[k.name].me, received: e.totals[k.name].them, label: fmt(e.totals[k.name].me) + " me · " + fmt(e.totals[k.name].them) + " them" }))),
      h("div", { class: "spacer" }), h("p", { class: "small muted", text: "What each one looks for: " + e.kinds.map((k) => KIND_LABELS[k.name] + " (" + k.looks_for + ")").join("; ") + "." })),
    h("div", { class: "panel" }, h("div", { class: "row" }, h("h2", { text: "One expression in detail" }), picker),
      h("h3", { text: KIND_LABELS[kind] + " over time" }), columns(e.over_time.map((b) => ({ period: b.period, sent: b[kind].me, received: b[kind].them }))),
      h("h3", { text: KIND_LABELS[kind] + " by person" }), people.length ? h("div", {}, legend(), bars(people.map((r) => ({ name: r.name, sent: r[kind].me, received: r[kind].them })), (r) => openProfile(r.name))) : h("p", { class: "muted", text: "None found in this selection." })),
    h("div", { class: "panel" }, h("h2", { text: "Message style" }), definition(st.definition),
      h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "Writer" }), h("th", { class: "n", text: "Avg words" }), h("th", { class: "n", text: "Long messages" }), h("th", { class: "n", text: "With emoji" }),
          h("th", { class: "n", text: "With !" }), h("th", { class: "n", text: "With ?" }), h("th", { class: "n", text: "ALL CAPS" }), h("th", { class: "n", text: "Starts lowercase" }))),
        h("tbody", {}, h("tr", {}, h("td", {}, h("strong", { text: "Me, overall" })), styleCells(st.me)), h("tr", {}, h("td", {}, h("strong", { text: "Everyone else, overall" })), styleCells(st.them)),
          st.by_person.map((r) => [h("tr", {}, h("td", {}, "Me with ", personLink(r.name)), styleCells(r.me)), h("tr", {}, h("td", { class: "muted", text: r.name + " with me" }), styleCells(r.them))]))))));
}

/* ---------- Hidden senders: codes, alerts, marketing ---------- */

async function renderHidden() {
  loading();
  const d = await request("/api/junk");
  const change = async (action, names) => {
    await post("/api/junk/change", { action, names });
    await loadShell();                 // totals and the name list at the top change too
    renderHidden();
  };
  const table = (rows, action, label) => h("div", { class: "scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", { text: "Sender" }), h("th", { class: "n", text: "Messages" }), h("th", { text: "Last" }), h("th", { text: "Why" }), h("th", { text: "Example" }), h("th", {}))),
    h("tbody", {}, rows.map((r) => h("tr", {},
      h("td", {}, h("button", { class: "link", text: r.name, onclick: () => { state.filters.contact = r.name; syncFilterInputs(); setTab("search"); } })),
      h("td", { class: "n", text: fmt(r.messages) }), h("td", { class: "nowrap", text: r.last_day || "" }),
      h("td", { class: "small", text: r.reason }), h("td", {}, h("div", { class: "sample", text: r.sample || "" })),
      h("td", {}, h("button", { type: "button", text: label, onclick: () => change(action, [r.name]) })))))));
  show(
    h("div", { class: "panel" }, h("h2", { text: "Hidden senders" }),
      definition(d.definition),
      h("p", {}, h("strong", { text: fmt(d.hidden_messages) + " messages from " + fmt(d.hidden.length) + " automated senders" }),
        " are set aside. They are left out of every screen, of anything sent to AI, and of what connected AI tools can search. Nothing is deleted. To look through them, tick ",
        h("em", { text: "Include automated senders" }), " on any screen with filters, or press a sender's name here."),
      d.hidden.length ? table(d.hidden, "keep", "Bring back") : h("p", { class: "muted", text: "No automated senders were found." })),
    d.suggested.length ? h("div", { class: "panel" }, h("h2", { text: "Probably junk: your call" }),
      h("p", { text: "These were not hidden, because the signs are weaker. Hide the ones that are not people." }),
      h("div", { class: "row" }, h("button", { class: "primary", text: "Hide all " + fmt(d.suggested.length), onclick: () => change("hide", d.suggested.map((r) => r.name)) })),
      h("div", { class: "spacer" }), table(d.suggested, "hide", "Hide")) : null,
    d.kept.length ? h("div", { class: "panel" }, h("h2", { text: "Brought back by you" }),
      h("p", { class: "muted small", text: "The rules would hide these, but you chose to keep them." }),
      table(d.kept, "hide", "Hide again")) : null);
}

/* ---------- Reading: AI digests of real conversations ---------- */

const usd = (n) => (n === null || n === undefined) ? "–" : "$" + Number(n).toFixed(n > 0 && n < 1 ? 3 : 2);
const pilot = { people: [], year: "", per_person: 40, timer: null };

/* Add the Anthropic API key from inside the app. It is stored in the Mac's Keychain. */
function keyPanel(onSaved) {
  const input = h("input", { type: "password", placeholder: "sk-ant-…", autocomplete: "off", "aria-label": "Anthropic API key" });
  const out = h("div", {});
  const save = async () => {
    out.replaceChildren(h("p", { class: "muted", text: "Saving and checking the key…" }));
    try {
      const result = await post("/api/key/save", { key: input.value });
      input.value = "";
      const missing = Object.entries(result.models).filter(([, ok]) => !ok).map(([model]) => model);
      out.replaceChildren(h("p", { text: missing.length
        ? "The key is saved, but this account cannot use: " + missing.join(", ") + ". The pilot will skip those."
        : "The key is saved and works with all three models." }),
        h("button", { class: "primary", text: "Continue", onclick: onSaved }));
    } catch (error) { out.replaceChildren(h("p", { class: "error", text: error.message })); }
  };
  return h("div", { class: "panel start-here" }, h("h2", { text: "Connect your Anthropic account" }),
    h("p", { text: "Reading conversations uses Anthropic's API, which is billed per use against the credit on your Anthropic account. It needs a key from that account." }),
    h("ol", {},
      h("li", { text: "In your browser, sign in at console.anthropic.com, open API keys, and create a key." }),
      h("li", { text: "Copy the key and paste it here." })),
    h("div", { class: "row grow" }, input, h("button", { class: "primary", text: "Save key", onclick: save })),
    out,
    h("p", { class: "small muted", text: "The key is stored in this Mac's Keychain, not in a file. Saving it sends no messages: it only asks Anthropic whether the key works." }));
}

function pilotResults(found) {
  if (!found.models.some((m) => m.messages_read)) return null;
  return h("div", { class: "panel" }, h("h2", { text: "What it cost, and what reading everything would cost" }),
    definition(found.definition),
    h("div", { class: "cards" }, card(usd(found.spent_usd), "spent so far"), card(usd(found.cap_usd), "pilot cap"),
      card(fmt(found.units_reviewed) + " of " + found.reviews_wanted, "conversations you have marked")),
    h("div", { class: "scroll" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", { text: "Model" }), h("th", { class: "n", text: "Digests" }), h("th", { class: "n", text: "Declined" }),
        h("th", { class: "n", text: "Messages read" }), h("th", { class: "n", text: "Spent" }), h("th", { class: "n", text: "Per 1,000 messages" }),
        h("th", { class: "n", text: "Whole history" }), h("th", { class: "n", text: "Whole history, batch" }),
        h("th", { class: "n", text: "Right" }), h("th", { class: "n", text: "Missing something" }), h("th", { class: "n", text: "Wrong" }))),
      h("tbody", {}, found.models.map((m) => h("tr", {}, h("td", {}, h("strong", { text: m.name })),
        h("td", { class: "n", text: fmt(m.digests) }), h("td", { class: "n", text: fmt(m.declined) + (m.unreadable ? " (+" + m.unreadable + " unreadable)" : "") }),
        h("td", { class: "n", text: fmt(m.messages_read) }), h("td", { class: "n", text: usd(m.spent_usd) }),
        h("td", { class: "n", text: usd(m.usd_per_thousand_messages) }), h("td", { class: "n", text: usd(m.whole_history_usd) }),
        h("td", { class: "n", text: usd(m.whole_history_batch_usd) }),
        h("td", { class: "n", text: fmt(m.right) }), h("td", { class: "n", text: fmt(m.missing) }), h("td", { class: "n", text: fmt(m.wrong) })))))),
    h("p", { class: "small muted", text: "Right, missing and wrong are your own marks from the Review screen. Whole history covers the " + fmt(found.messages_in_history) + " messages left after automated senders are set aside." }),
    reportBox());
}

/* A numbers-only summary to paste to whoever maintains the tool: cost, tokens, speed, waits, failures and marks. */
function reportBox() {
  const out = h("div", {});
  const make = async () => {
    try {
      const report = await request("/api/reading/report");
      const box = h("textarea", { class: "report-text", readonly: true, rows: "16", "aria-label": "Pilot report" });
      box.value = report.text;
      const status = h("span", { class: "small muted" });
      const copy = async () => {
        try { await navigator.clipboard.writeText(report.text); status.textContent = "Copied. Paste it into your conversation with Claude."; }
        catch (error) { box.select(); status.textContent = "Select all of the text above and copy it."; }
      };
      out.replaceChildren(box, h("div", { class: "row" }, h("button", { class: "primary", text: "Copy", onclick: copy }), status));
    } catch (error) { out.replaceChildren(h("p", { class: "error", text: error.message })); }
  };
  return h("div", {}, h("div", { class: "spacer" }),
    h("div", { class: "row" }, h("button", { text: "Make the pilot report", onclick: make }),
      h("span", { class: "small muted", text: "Numbers only: cost, tokens, speed, waits, failures and your marks. No names and no message text." })),
    out);
}

function pilotProgress(st) {
  const rows = st.models.map((m) => {
    const done = m.ok + m.declined + m.unreadable + m.failed;
    const fill = h("div", { class: "bar-sent" });
    fill.style.width = (m.total ? Math.round(100 * done / m.total) : 100) + "%";   // set from code: the page's security rules forbid inline styles
    return h("div", { class: "bar-row" }, h("div", { class: "name", text: m.name }),
      h("div", { class: "bar-track" }, fill),
      h("div", { class: "val", text: done + " of " + m.total + (m.declined ? ", " + m.declined + " declined" : "") + (m.failed ? ", " + m.failed + " failed" : "") }));
  });
  const headline = st.running ? "Reading…" : st.stopped_at_cap ? "Stopped at the spending cap" : st.stopped_by_you ? "Stopped" : "Finished";
  return h("div", { class: "panel" }, h("h2", { text: headline }),
    h("p", { text: "Spent " + usd(st.spent_usd) + " of the " + usd(st.cap_usd) + " cap." }),
    h("div", { class: "bars" }, rows),
    st.problems.length ? h("div", { class: "notice" }, h("strong", { text: "Problems: " }), st.problems.join("  ")) : null,
    !st.running && st.models.some((m) => m.failed) ? h("p", { class: "small muted", text: "Some conversations were not read. Nothing was charged for those. Go back to the pilot and send again to read only the ones still missing." }) : null,
    h("div", { class: "spacer" }),
    st.running
      ? h("div", { class: "row" }, h("button", { text: "Stop", onclick: async () => { await post("/api/reading/stop", {}); } }),
          h("span", { class: "small muted", text: "You can leave this screen. Reading continues while iMessage Explorer stays open, and digests already read are kept." }))
      : h("div", { class: "row" }, h("button", { class: "primary", text: "Review the digests", onclick: () => setTab("review") }),
          h("button", { text: "Back to the pilot", onclick: () => { pilot.showProgress = false; renderPilot(); } })));
}

async function watchPilot() {
  clearTimeout(pilot.timer);
  if (state.tab !== "pilot") return;
  const [st, found] = await Promise.all([request("/api/reading/status"), request("/api/reading/results")]);
  if (state.tab !== "pilot") return;
  show(pilotProgress(st), pilotResults(found));
  if (st.running) pilot.timer = setTimeout(() => watchPilot().catch(() => {}), 2000);
}

async function renderPilot() {
  clearTimeout(pilot.timer);
  const st = await request("/api/reading/status");
  if (st.running || pilot.showProgress) { pilot.showProgress = true; return watchPilot(); }
  if (!st.key.key_found) { show(keyPanel(renderPilot)); return; }
  const query = new URLSearchParams({ year: pilot.year, people: pilot.people.join("|"), per_person: pilot.per_person });
  const [plan, found] = await Promise.all([request("/api/reading/plan?" + query.toString()), request("/api/reading/results")]);
  pilot.year = plan.year;
  pilot.people = plan.people.map((p) => p.name);

  const year = h("input", { type: "number", value: plan.year, min: "2000", max: "2100", "aria-label": "Year", class: "year" });
  const names = [0, 1, 2].map((i) => h("input", { list: "contact-list", value: pilot.people[i] || "", placeholder: "Person " + (i + 1), autocomplete: "off", "aria-label": "Person " + (i + 1) }));
  const size = h("select", { "aria-label": "Conversations per person" }, [20, 40, 60, 100].map((n) => h("option", { value: n, text: n + " conversations each" })));
  size.value = String(plan.per_person);
  const refresh = () => { pilot.year = year.value; pilot.people = names.map((n) => n.value.trim()).filter(Boolean); pilot.per_person = Number(size.value); renderPilot(); };

  const nothingLeft = plan.models.every((m) => !m.units_to_read);
  const send = async (button) => {
    button.disabled = true;
    try {
      await post("/api/reading/start", { confirm: true, year: pilot.year, people: pilot.people, per_person: pilot.per_person });
      pilot.showProgress = true;
      watchPilot();
    } catch (error) { button.disabled = false; sendOut.replaceChildren(h("p", { class: "error", text: error.message })); }
  };
  const sendOut = h("div", {});

  show(
    h("div", { class: "panel" }, h("h2", { text: "Reading pilot" }),
      h("p", { text: "Three AI models each read the same sample of your real conversations and write a digest of every one: what it was about, how it felt, what happened, any friction, and what was promised. You then mark the digests right or wrong without being told which model wrote which. The result tells you which model to trust with your whole history, and what that will cost." }),
      h("div", { class: "row" }, h("label", {}, "Year ", year), names, size, h("button", { text: "Update preview", onclick: refresh })),
      h("p", { class: "small muted", text: "It starts with the three people you messaged most that year. Change any name to read someone else." })),
    h("div", { class: "panel" }, h("h2", { text: "This is what would leave your Mac" }),
      plan.units ? h("p", {}, h("strong", { text: fmt(plan.messages) + " messages" }), " in " + fmt(plan.units) + " conversations, from " + plan.first_day + " to " + plan.last_day +
        ", would be sent to Anthropic and read by three models. Names appear as they do in your contacts. Other people's words are included.")
        : h("p", { text: "No conversations were found for those people in " + plan.year + "." }),
      h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "Person" }), h("th", { class: "n", text: "Messages that year" }), h("th", { class: "n", text: "Conversations that year" }),
          h("th", { class: "n", text: "Conversations in the sample" }), h("th", { class: "n", text: "Messages in the sample" }))),
        h("tbody", {}, plan.people.map((p) => h("tr", {}, h("td", { text: p.name }), h("td", { class: "n", text: fmt(p.messages_in_year) }), h("td", { class: "n", text: fmt(p.units_in_year) }),
          h("td", { class: "n", text: fmt(p.units_chosen) }), h("td", { class: "n", text: fmt(p.messages_chosen) })))))),
      h("div", { class: "spacer" }), h("h3", { text: "Estimated cost" }),
      h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "Model" }), h("th", { class: "n", text: "Still to read" }), h("th", { class: "n", text: "Already read" }), h("th", { class: "n", text: "Rough estimate" }))),
        h("tbody", {}, plan.models.map((m) => h("tr", {}, h("td", { text: m.name }), h("td", { class: "n", text: fmt(m.units_to_read) }), h("td", { class: "n", text: fmt(m.already_read) }), h("td", { class: "n", text: usd(m.estimated_cost_usd) })))))),
      h("p", {}, "Rough total: ", h("strong", { text: usd(plan.estimated_cost_usd) }), ". Spent so far: " + usd(plan.spent_usd) + ". Cap: " + usd(plan.cap_usd) + "."),
      h("p", { class: "small muted", text: "The estimate is a guess made without sending anything. Real cost is measured as it runs. The cap is a hard stop: before each request the app checks that even the dearest possible reply would still fit, and stops if not. A conversation already read is never paid for twice." }),
      nothingLeft
        ? h("p", { text: "Everything in this sample has been read." })
        : h("button", { class: "primary", text: "Send these " + fmt(plan.units) + " conversations to the AI", disabled: !plan.units || plan.remaining_usd <= 0, onclick: (event) => send(event.currentTarget) }),
      sendOut,
      found.units_ready ? h("p", {}, h("button", { class: "link", text: "Review the digests already read", onclick: () => setTab("review") })) : null),
    pilotResults(found));
}

/* One model's digest, shown under a letter instead of the model's name. */
function digestCard(unitId, reading, onMarked) {
  const d = reading.digest;
  const list = (title, items, render) => items && items.length ? [h("h4", { text: title }), h("ul", { class: "tight" }, items.map((item) => h("li", { text: render(item) })))] : null;
  const note = h("input", { type: "text", value: reading.note || "", placeholder: "Optional: what was wrong or missing", "aria-label": "Note for reading " + reading.slot });
  const buttons = [["right", "Right"], ["missing", "Missing something"], ["wrong", "Wrong"]].map(([value, label]) =>
    h("button", { type: "button", class: "verdict " + value + (reading.verdict === value ? " on" : ""), "aria-pressed": reading.verdict === value ? "true" : "false", text: label, onclick: async (event) => {
      const pressed = event.currentTarget;      // read now: the browser clears it once the click has been handled
      try {
        await post("/api/reading/review", { unit_id: unitId, slot: reading.slot, verdict: value, note: note.value });
        reading.verdict = value;
        for (const b of buttons) { b.classList.toggle("on", b === pressed); b.setAttribute("aria-pressed", b === pressed ? "true" : "false"); }
        onMarked();
      } catch (error) { alertBox.textContent = error.message; }
    } }));
  const alertBox = h("p", { class: "error small" });
  return h("div", { class: "reading-card" }, h("h3", { text: "Reading " + reading.slot }),
    reading.status !== "ok"
      ? h("p", { class: "muted", text: reading.status === "declined" ? "This model declined to read this conversation." : "This model's reply could not be read." })
      : [h("p", { text: d.summary }),
         h("p", {}, h("span", { class: "chip", text: d.closeness }), " ", d.tone),
         d.topics.length ? h("div", { class: "chips" }, d.topics.map((t) => h("span", { class: "chip", text: t }))) : null,
         list("What happened", d.events, (e) => e.date + ": " + e.what),
         list("Friction", d.tensions, (t) => t.what + " (" + t.resolution + ")"),
         list("Promises", d.promises, (p) => p.who + ": " + p.what),
         d.people.length ? h("p", { class: "small" }, h("strong", { text: "People: " }), d.people.join(", ")) : null,
         d.places.length ? h("p", { class: "small" }, h("strong", { text: "Places: " }), d.places.join(", ")) : null,
         d.notable ? h("p", {}, h("strong", { text: "Worth remembering: " }), d.notable) : null],
    reading.status === "ok" ? [h("div", { class: "row verdicts" }, buttons), note, alertBox] : null);
}

async function renderReview(unitId) {
  const queue = await request("/api/reading/queue");
  if (!queue.ready) {
    show(h("div", { class: "panel" }, h("h2", { text: "Nothing to review yet" }),
      h("p", { text: "Run the reading pilot first. Digests appear here as soon as at least two models have read a conversation." }),
      h("button", { class: "primary", text: "Go to the pilot", onclick: () => setTab("pilot") })));
    return;
  }
  const id = unitId || queue.next || queue.all[0];
  const data = await request("/api/reading/unit?id=" + encodeURIComponent(id));
  const position = queue.all.indexOf(id);
  const progress = h("strong", { text: queue.reviewed + " of " + queue.wanted });
  const refreshProgress = async () => { const q = await request("/api/reading/queue"); progress.textContent = q.reviewed + " of " + q.wanted; };
  const go = (offset) => renderReview(queue.all[(position + offset + queue.all.length) % queue.all.length]);
  show(
    h("div", { class: "panel" }, h("h2", { text: "Review: " + data.unit.counterpart + ", " + (data.unit.first_day === data.unit.last_day ? data.unit.first_day : data.unit.first_day + " to " + data.unit.last_day) }),
      h("p", {}, "Read the conversation on the left, then mark each reading. You are not told which model wrote which, and the letters are shuffled for every conversation. Marked so far: ", progress, " conversations wanted before choosing a model."),
      h("div", { class: "row" }, h("button", { text: "Previous", onclick: () => go(-1) }), h("button", { text: "Skip", onclick: () => go(1) }),
        h("button", { class: "primary", text: "Next unmarked", onclick: () => renderReview() }),
        h("span", { class: "small muted", text: "Conversation " + (position + 1) + " of " + queue.all.length + " read so far." }))),
    h("div", { class: "review-split" },
      h("div", { class: "panel original" }, h("h3", { text: "The conversation (" + fmt(data.unit.messages) + " messages)" }),
        h("div", { class: "original-scroll" }, data.messages.map((m) => messageNode(m, [])))),
      h("div", { class: "readings" }, data.readings.map((r) => digestCard(id, r, refreshProgress)))));
}

/* ---------- Full read: a digest of every conversation ---------- */

const full = { timer: null };

function fullProgress(st) {
  const target = Math.max(st.target || 0, st.read + st.being_read);
  const fill = h("div", { class: "bar-sent" });
  fill.style.width = (target ? Math.round(100 * st.read / target) : 0) + "%";
  const busy = st.active || st.being_read > 0;
  const headline = busy ? (st.being_read ? "Anthropic is reading " + fmt(st.being_read) + " stretches" : "Preparing the next wave…")
    : st.ended === "finished" ? "The full read is finished" : st.ended === "stopped at the cap" ? "Stopped at the spending cap"
    : st.ended === "stopped by you" ? "Stopped" : st.ended === "stopped by a problem" ? "Stopped by a problem" : "Not running";
  return h("div", { class: "panel" }, h("h2", { text: headline }),
    h("div", { class: "cards" }, card(fmt(st.read) + (target ? " of " + fmt(target) : ""), "stretches read"), card(fmt(st.messages_read), "messages read"),
      card(usd(st.spent_usd) + " of " + usd(st.cap_usd), "spent"), card(fmt(st.being_read), "being read now")),
    h("div", { class: "bar-track tall" }, fill),
    busy ? h("p", { text: "Batches usually finish within an hour and can take up to a day. You can quit iMessage Explorer and close the Terminal window: Anthropic carries on reading. Open the app again later and it collects the results and sends the next wave by itself." }) : null,
    st.last_checked && busy ? h("p", { class: "small muted", text: "Last checked " + st.last_checked + ". Checks every minute while the app is open." }) : null,
    st.problem ? h("div", { class: "notice" }, h("strong", { text: "Problem: " }), st.problem, busy ? " The app will keep trying." : "") : null,
    st.not_read || st.declined || st.problems.length ? h("p", { class: "small muted", text:
      (st.not_read ? fmt(st.not_read) + " stretches could not be read after two tries. " : "") +
      (st.declined ? fmt(st.declined) + " were declined by the model. " : "") +
      (st.problems.length ? "Reasons given: " + st.problems.map((p) => p.problem.replace("full read: ", "") + " (" + p.times + ")").join("; ") + "." : "") }) : null,
    st.batches.length ? h("details", { class: "definition" }, h("summary", { text: "Batches sent (" + st.batches.length + ")" }),
      h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "Batch" }), h("th", { text: "Sent" }), h("th", { class: "n", text: "Stretches" }), h("th", { text: "State" }),
          h("th", { class: "n", text: "Read" }), h("th", { class: "n", text: "Failed" }), h("th", { class: "n", text: "Cost" }))),
        h("tbody", {}, st.batches.map((b) => h("tr", {}, h("td", { text: b.id }), h("td", { class: "nowrap", text: b.created_at }), h("td", { class: "n", text: fmt(b.requests) }),
          h("td", { text: b.status === "collected" ? "collected " + b.collected_at : "being processed" }),
          h("td", { class: "n", text: fmt(b.succeeded) }), h("td", { class: "n", text: fmt(b.errored + b.not_processed) }), h("td", { class: "n", text: usd(b.spent_usd) }))))))) : null,
    h("div", { class: "row" },
      busy ? h("button", { text: "Stop the full read", onclick: async () => { await post("/api/full/stop", {}); watchFull(); } }) : null,
      st.read ? h("button", { class: busy ? "" : "primary", text: "Read the digests", onclick: () => setTab("digests") }) : null,
      !busy ? h("button", { text: st.ended === "finished" ? "Check for anything new to read" : "Review and continue", onclick: () => { full.plan = true; renderFull(); } }) : null));
}

async function watchFull() {
  clearTimeout(full.timer);
  if (state.tab !== "full") return;
  const st = await request("/api/full/status");
  if (state.tab !== "full") return;
  show(fullProgress(st));
  if (st.active || st.being_read > 0) full.timer = setTimeout(() => watchFull().catch(() => {}), 5000);
}

async function renderFull() {
  clearTimeout(full.timer);
  const st = await request("/api/full/status");
  if (!st.key.key_found) { show(keyPanel(renderFull)); return; }
  if ((st.active || st.being_read > 0 || st.read > 0) && !full.plan) return watchFull();
  full.plan = false;
  loading("Working out what there is to read. This takes a few seconds on a large history…");
  const plan = await request("/api/full/plan");
  const [normal, close] = plan.models;

  const name = h("input", { list: "contact-list", placeholder: "Type a name exactly as it appears in the app", autocomplete: "off", "aria-label": "Person to read with the stronger model" });
  const saveClose = async (names) => { await post("/api/full/close-people", { names }); full.plan = true; renderFull(); };
  const missing = plan.close_people.filter((n) => !plan.close_people_found.includes(n));
  const out = h("div", {});
  const begin = async (button) => {
    button.disabled = true;
    try { await post("/api/full/start", { confirm: true }); watchFull(); }
    catch (error) { button.disabled = false; out.replaceChildren(h("p", { class: "error", text: error.message })); }
  };
  const row = (m) => h("tr", {}, h("td", {}, h("strong", { text: m.name })), h("td", { class: "n", text: fmt(m.units) }), h("td", { class: "n", text: fmt(m.messages) }),
    h("td", { class: "n", text: fmt(m.already_read) }), h("td", { class: "n", text: fmt(m.being_read) }), h("td", { class: "n", text: fmt(m.to_send) }),
    h("td", { class: "n", text: usd(m.estimated_cost_usd) }));

  show(
    h("div", { class: "panel" }, h("h2", { text: "Full read" }),
      h("p", { text: "Every conversation in your history is read and given a digest: what it was about, how it felt, what happened, any friction, and what was promised. " + normal.name + " reads everything, except the people you name here, who are read by " + close.name + "." }),
      h("h3", { text: "Read by " + close.name }),
      plan.close_people.length ? h("div", { class: "chips" }, plan.close_people.map((n) => h("span", { class: "chip" }, n + " ",
        h("button", { class: "link", text: "remove", "aria-label": "Remove " + n, onclick: () => saveClose(plan.close_people.filter((x) => x !== n)) }))))
        : h("p", { class: "muted", text: "Nobody yet. Everyone would be read by " + normal.name + "." }),
      missing.length ? h("div", { class: "notice", text: "No one-to-one conversation was found under: " + missing.join(", ") + ". Names must match the app exactly; pick from the suggestions as you type." }) : null,
      h("div", { class: "row grow" }, name, h("button", { text: "Add", onclick: () => { if (name.value.trim()) saveClose(plan.close_people.concat([name.value.trim()])); } }))),
    h("div", { class: "panel" }, h("h2", { text: "This is what would leave your Mac" }),
      h("p", {}, h("strong", { text: fmt(plan.messages) + " messages" }), " in " + fmt(plan.conversations) + " conversations (" + fmt(plan.people) + " people and " + fmt(plan.groups) +
        " group chats), from " + plan.first_day + " to " + plan.last_day + ", cut into " + fmt(plan.units) + " stretches. Names appear as they do in your contacts. Other people's words are included."),
      h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "Read by" }), h("th", { class: "n", text: "Stretches" }), h("th", { class: "n", text: "Messages" }), h("th", { class: "n", text: "Already read" }),
          h("th", { class: "n", text: "Being read" }), h("th", { class: "n", text: "Still to send" }), h("th", { class: "n", text: "Estimated cost" }))),
        h("tbody", {}, row(normal), row(close)))),
      h("p", {}, "Estimated cost of what is left: ", h("strong", { text: usd(plan.estimated_cost_usd) }), ". Spent so far: " + usd(plan.spent_usd) + ". Cap: " + usd(plan.cap_usd) + "."),
      h("p", { class: "small muted", text: (plan.estimate_from_your_messages ? "The estimate comes from what the pilot measured on your own messages, at the half-price batch rate. " : "No pilot results were found, so the estimate uses round figures. ") +
        "Left out: automated senders, and " + fmt(plan.too_short.units) + " exchanges too short to say anything (" + fmt(plan.too_short.messages) + " messages)." }),
      definition("Conversations are sent in waves. Each wave is sized so that even if every conversation still being read cost twice the estimate, the total would stay under the cap; the true cost of each wave is recorded when its results arrive, before the next is sized. The people read by the stronger model go first, then the busiest conversations, so a stop or the cap falls on the quietest ones. A conversation already read, including in the pilot, is never sent again."),
      plan.to_send
        ? h("button", { class: "primary", text: (st.read ? "Continue: send " : "Start the full read: send ") + fmt(plan.to_send) + " stretches", disabled: plan.remaining_usd <= 0, onclick: (event) => begin(event.currentTarget) })
        : h("p", { text: "Everything has been read." }),
      out,
      st.read ? h("p", {}, h("button", { class: "link", text: "Back to progress", onclick: () => watchFull() })) : null));
}

/* ---------- Digests: the story of a conversation, stretch by stretch ---------- */

const digestView = { name: "", group: false, newest: false };

function digestBlock(d) {
  const g = d.digest;
  const list = (title, items, render) => items && items.length ? [h("h4", { text: title }), h("ul", { class: "tight" }, items.map((item) => h("li", { text: render(item) })))] : null;
  const holder = h("div", {});
  const toggle = h("button", { class: "link small", text: "Show the messages", onclick: async () => {
    if (holder.childNodes.length) { holder.replaceChildren(); toggle.textContent = "Show the messages"; return; }
    try {
      const data = await request("/api/reading/unit?id=" + encodeURIComponent(d.unit_id));
      holder.replaceChildren(h("div", { class: "context" }, data.messages.map((m) => messageNode(m, []))));
      toggle.textContent = "Hide the messages";
    } catch (error) { holder.replaceChildren(h("p", { class: "error", text: error.message })); }
  } });
  return h("div", { class: "reading-card" },
    h("h3", { text: d.first_day === d.last_day ? d.first_day : d.first_day + " to " + d.last_day }),
    h("p", { text: g.summary }),
    h("p", {}, h("span", { class: "chip", text: g.closeness }), " ", g.tone),
    g.topics.length ? h("div", { class: "chips" }, g.topics.map((t) => h("span", { class: "chip", text: t }))) : null,
    list("What happened", g.events, (e) => e.date + ": " + e.what),
    list("Friction", g.tensions, (t) => t.what + " (" + t.resolution + ")"),
    list("Promises", g.promises, (p) => p.who + ": " + p.what),
    g.notable ? h("p", {}, h("strong", { text: "Worth remembering: " }), g.notable) : null,
    h("p", { class: "small muted" }, fmt(d.messages) + " messages, read by " + d.read_by + ". ", toggle),
    holder);
}

async function renderDigests() {
  loading();
  const found = await request("/api/full/people");
  if (!found.people.length) {
    show(h("div", { class: "panel" }, h("h2", { text: "No digests yet" }),
      h("p", { text: "Digests appear here as conversations are read, by the pilot or the full read." }),
      h("button", { class: "primary", text: "Go to the full read", onclick: () => setTab("full") })));
    return;
  }
  if (!found.people.some((p) => p.name === digestView.name && p.is_group === digestView.group)) {
    digestView.name = found.people[0].name; digestView.group = found.people[0].is_group;
  }
  const who = h("select", { "aria-label": "Whose digests" }, found.people.map((p, i) =>
    h("option", { value: String(i), text: p.name + (p.is_group ? " (group)" : "") + ": " + fmt(p.digests) + " digests, " + p.first_day.slice(0, 4) + " to " + p.last_day.slice(0, 4) })));
  who.value = String(found.people.findIndex((p) => p.name === digestView.name && p.is_group === digestView.group));
  who.addEventListener("change", () => { const p = found.people[Number(who.value)]; digestView.name = p.name; digestView.group = p.is_group; renderDigests(); });
  const order = h("select", { "aria-label": "Order" }, h("option", { value: "", text: "Oldest first" }), h("option", { value: "1", text: "Newest first" }));
  order.value = digestView.newest ? "1" : "";
  order.addEventListener("change", () => { digestView.newest = order.value === "1"; renderDigests(); });
  const listing = h("div", { class: "readings" });
  const more = h("div", {});
  let offset = 0;
  const load = async () => {
    const query = new URLSearchParams({ name: digestView.name, group: digestView.group ? "1" : "", offset: String(offset), newest: digestView.newest ? "1" : "" });
    const page = await request("/api/full/digests?" + query.toString());
    listing.append(...page.digests.map(digestBlock));
    offset += page.digests.length;
    more.replaceChildren(offset < page.total
      ? h("button", { text: "Show more (" + fmt(page.total - offset) + " left)", onclick: load })
      : h("p", { class: "muted small", text: "That is all " + fmt(page.total) + "." }));
  };
  show(h("div", { class: "panel" }, h("h2", { text: "Digests" }),
    h("p", { text: "One person or group at a time, in date order: the story of the conversation, stretch by stretch. Each digest is one model's reading, so open the messages beside anything that surprises you." }),
    h("div", { class: "row" }, who, order)), listing, h("div", { class: "spacer" }), more);
  await load();
}

/* Ready-made questions for the Ask AI screen. */
const AI_PRESETS = {
  tone: "Describe the emotional tone of these messages and how it changes over time. Point out warm stretches, tense stretches and turning points, with dates. Note what each person tends to bring emotionally. Be candid but fair, and do not speculate beyond what the messages show.",
  holistic: "Give a holistic read of this relationship from the messages. What role does this person play in my life? What are the main themes we talk about? How have closeness and tone changed over time? What does each of us tend to give and ask for? What moments stand out, and does anything look unresolved? Ground each point in dated examples. Be candid but fair, and do not speculate beyond what the messages show.",
  themes: "What are the main things we talk about, roughly in order of how much? For each, give a one-line summary and a few dated examples.",
};

Object.assign(SCREENS, {
  home: renderHome, ranking: renderRanking, circles: renderCircles, profile: renderProfile, people: renderPeople,
  search: renderSearch, topics: renderTopics,
  trends: renderTrends, replies: renderReplies, reactions: renderReactions, tone: renderTone, words: renderWords,
  expressions: renderExpressions, language: renderLanguage, sharing: renderSharing,
  ask: renderAsk, connect: renderConnect, pilot: renderPilot, review: () => renderReview(), hidden: renderHidden,
  full: renderFull, digests: renderDigests,
});

start();
