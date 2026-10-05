/* L2 + L4 + L6: the Game view -- timeline of a run, In -> out of one question, map path, live. */
"use strict";

(function () {

  // Router names; the fallbacks read runs logged before the rename (router 1 / Gatekeeper).
  function routerName(r) {
    if (r === "relevance" || r === 1 || r === "persona") return "Relevance Router";
    if (r === "question" || r === 2 || r === "gatekeeper") return "Question Router";
    return "router " + Lab.esc(r);
  }
  function relevanceQid(c) { return c.relevance_qid || c.router1_qid; }
  function questionQid(c) { return c.question_qid || c.gatekeeper_qid || c.router2_qid; }
  var V = Lab.views.game = {
    runs: [], run: null, runInfo: null, kind: null, entries: {}, order: [], rev: 0,
    selected: null, live: false, follow: true, timer: null,
    ioTab: "prompt", lastIo: null, busy: false
  };

  V.init = function () {
    document.getElementById("game-run").addEventListener("change", function (e) {
      V.open(e.target.value, null);
    });
    document.getElementById("game-kind").addEventListener("change", function (e) {
      V.kind = e.target.value;
      Lab.store.set("lab.game.kind", V.kind);
      V.reload();
    });
    var live = document.getElementById("game-live");
    live.addEventListener("change", function () { V.setLive(live.checked); });
    var follow = document.getElementById("game-follow");
    follow.addEventListener("change", function () { V.follow = follow.checked; });
    V.kind = Lab.store.get("lab.game.kind", "play");
  };

  V.shown = function () {
    V.loadRuns().then(function () {
      if (!V.run) {
        var saved = Lab.store.get("lab.game.run", null);
        var pick = V.runs.filter(function (r) { return r.live; })[0] ||   // a game running now first
          V.runs.filter(function (r) { return r.id === saved; })[0] ||
          V.runs.filter(function (r) { return r.id.indexOf("phase12c2-jev-live") >= 0; })[0] || V.runs[0];
        if (pick) V.open(pick.id, null);
      }
    });
    V.startPolling();
  };

  /* "Jev (p1, blue)", never a bare player number. */
  V.seat = function (player) {
    var s = ((V.runInfo && V.runInfo.seats) || []).filter(function (x) { return x.id === player; })[0];
    return s ? s.label : "p" + player;
  };

  V.loadRuns = function () {
    return Lab.get("/api/runs").then(function (res) {
      V.runs = res.runs.slice().sort(function (a, b) { return b.mtime - a.mtime; });
      var sel = document.getElementById("game-run");
      sel.innerHTML = V.runs.map(function (r) {
        var d = new Date(r.mtime * 1000), pad = function (n) { return (n < 10 ? "0" : "") + n; };
        var when = pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
        var who = (r.seats || []).map(function (s) { return s.label; }).join(" vs ");
        var label = when + "  ·  " + r.id.replace(/^results\//, "") + "  ·  " + (who ? who + "  ·  " : "") + (r.adapter || "?") + " " + (r.version || "") +
          "  ·  Q&A " + (r.qa_version || "?") + "  ·  " + r.rows + " q" + (r.live ? "  ·  LIVE" : "");
        return '<option value="' + Lab.esc(r.id) + '"' + (r.id === V.run ? " selected" : "") + ">" + Lab.esc(label) + "</option>";
      }).join("");
    }).catch(function (e) { Lab.toast("Runs: " + e.message, true); });
  };

  V.setLive = function (on) {
    V.live = on;
    document.getElementById("game-run").disabled = on;
    if (on) {
      V.run = null;
      V.poll(true);
    } else {
      V.open(document.getElementById("game-run").value || V.run, null);
    }
  };

  V.open = function (rid, kind) {
    V.run = rid;
    Lab.store.set("lab.game.run", rid);
    if (kind) V.kind = kind;
    V.reload();
  };

  V.reset = function () {
    V.gen = (V.gen || 0) + 1;          // responses of an older generation are dropped
    V.entries = {}; V.order = []; V.rev = 0; V.selected = null;
    document.getElementById("timeline").innerHTML = "";
    document.getElementById("io-panel").innerHTML = '<p class="muted">Pick a question on the left.</p>';
  };

  V.reload = function () {
    V.reset();
    return V.fetch(false);
  };

  V.fetch = function (incremental) {
    if (V.busy) {
      if (!incremental) V.needReload = true;
      return Promise.resolve();
    }
    V.busy = true;
    var gen = V.gen;
    var params = { run: V.live ? "newest" : V.run, kind: V.kind === "all" ? "" : V.kind, since: incremental ? V.rev : 0 };
    return Lab.get("/api/timeline", params).then(function (res) {
      if (gen !== V.gen) return;           // the run or kind changed while this was in flight
      if (V.live && res.run.id !== V.run) {       // a new game started: start over on it
        V.run = res.run.id;
        V.reset();
        params.since = 0;
        V.busy = false;
        return V.fetch(false);
      }
      V.runInfo = res.run;
      V.run = res.run.id;
      var runSel = document.getElementById("game-run");
      if (runSel.value !== V.run) runSel.value = V.run;
      V.fillKinds(res.run);
      if (!(V.kind in (res.run.kinds || {})) && V.kind !== "all" && !incremental &&
          res.run.rows > 0 && !res.run.live && !V.live) {
        V.kind = V.defaultKind(res.run);
        V.busy = false;
        return V.reload();
      }
      var fresh = [];
      res.entries.forEach(function (e) {
        if (!V.entries[e.idx]) fresh.push(e.idx);
        V.entries[e.idx] = e;
      });
      V.rev = Math.max(V.rev, res.rev);
      V.order = Object.keys(V.entries).map(Number).sort(function (a, b) {
        var x = V.entries[a], y = V.entries[b];
        return ((x.askedTime || 0) - (y.askedTime || 0)) || ((x.qid || 0) - (y.qid || 0)) || (a - b);
      });
      if (res.entries.length || !incremental) V.render(incremental ? fresh : []);
      V.status();
      if (incremental && fresh.length && V.follow) {
        V.select(V.order[V.order.length - 1], true);
      } else if (!incremental && V.selected === null && V.order.length && (V.live || V.runInfo.live)) {
        V.select(V.order[V.order.length - 1], true);
      }
    }).catch(function (e) {
      document.getElementById("game-status").textContent = e.message;
    }).then(function () {
      V.busy = false;
      if (V.needReload) { V.needReload = false; V.fetch(false); }
    });
  };

  V.defaultKind = function (run) {
    var k = run.kinds || {};
    if (k.play) return "play";
    if (k.pass_order) return "pass_order";
    var best = null;
    Object.keys(k).forEach(function (x) { if (!best || k[x] > k[best]) best = x; });
    return best || "play";
  };

  V.fillKinds = function (run) {
    var sel = document.getElementById("game-kind");
    var kinds = run.kinds || {};
    var names = Object.keys(kinds).sort(function (a, b) { return kinds[b] - kinds[a]; });
    var html = names.map(function (k) {
      return '<option value="' + Lab.esc(k) + '"' + (k === V.kind ? " selected" : "") + ">" + Lab.esc(k) + " (" + kinds[k] + ")</option>";
    }).join("") + '<option value="all"' + (V.kind === "all" ? " selected" : "") + ">all kinds</option>";
    if (sel.innerHTML !== html) sel.innerHTML = html;
  };

  V.status = function () {
    var r = V.runInfo;
    if (!r) return;
    var secs = Math.max(0, Math.round(Date.now() / 1000 - r.mtime));
    var ago = secs < 120 ? secs + " s" : secs < 7200 ? Math.round(secs / 60) + " min" :
      secs < 172800 ? Math.round(secs / 3600) + " h" : Math.round(secs / 86400) + " days";
    document.getElementById("game-status").innerHTML =
      (r.live ? '<span class="pill err">live</span> ' : r.finished ? '<span class="pill">finished</span> ' : '<span class="pill warn">not finished</span> ') +
      Lab.esc(V.order.length + " question(s) shown · model " + (r.adapter || "?") + " " + (r.version || "") +
        " · Q&A " + (r.qa_version || "?") + (r.qa ? " (pinned snapshot)" : "") + " · last write " + ago + " ago");
  };

  function chip(o, e) {
    var used = e.game.choice || e.choice;
    var cls = "opt" + (o === used ? " chosen" : "") + (o === e.rule ? " rule" : "");
    var p = "";
    if (o === used && e.probs && e.probs[o] !== undefined) p = '<span class="p">' + Math.round(e.probs[o] * 100) + "%</span>";
    return '<span class="' + cls + '" title="' + (o === e.rule ? "the rule's pick" : "") + '">' + Lab.esc(o) + p + "</span>";
  }

  V.rowHtml = function (e) {
    var trig = (e.trigger || []).map(function (p) {
      return '<div><span class="part">' + Lab.esc(p.part) + "</span> " +
        p.events.map(function (x) { return Lab.esc(x.text); }).join("; ") + "</div>";
    }).join("");
    var a = e.game.applied || {};
    var by = e.game.by === "rule" ? ' <span class="by-rule">by rule</span>' :
      (e.outcome && e.outcome !== "posted" ? ' <span class="by-rule">' + Lab.esc(e.outcome) + "</span>" : "");
    return '<div class="tl-row' + (e.idx === V.selected ? " sel" : "") + '" data-idx="' + e.idx + '">' +
      '<div class="min">' + (e.minute === null || e.minute === undefined ? "?" : e.minute.toFixed(1)) +
      '<span class="q">q#' + e.qid + (V.kind === "all" ? " " + Lab.esc(e.kind) : "") + "</span></div>" +
      '<div class="trig">' + trig + "</div>" +
      "<div>" + e.options.map(function (o) { return chip(o, e); }).join("") + by + "</div>" +
      '<div class="did ' + Lab.esc(a.state || "") + '">' + Lab.esc(a.text || "") + "</div></div>";
  };

  V.render = function (fresh) {
    var box = document.getElementById("timeline");
    var atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    box.innerHTML = V.order.length ? V.order.map(function (i) { return V.rowHtml(V.entries[i]); }).join("") :
      '<p class="muted" style="padding:12px">No ' + Lab.esc(V.kind) + " questions in this run yet.</p>";
    (fresh || []).forEach(function (i) {
      var row = box.querySelector('[data-idx="' + i + '"]');
      if (row) row.classList.add("fresh");
    });
    box.querySelectorAll(".tl-row").forEach(function (row) {
      row.addEventListener("click", function () { V.select(+row.dataset.idx); });
    });
    if ((V.live || (V.runInfo && V.runInfo.live)) && V.follow && atBottom) box.scrollTop = box.scrollHeight;
  };

  V.select = function (idx, scroll) {
    V.selected = idx;
    var box = document.getElementById("timeline");
    box.querySelectorAll(".tl-row").forEach(function (r) { r.classList.toggle("sel", +r.dataset.idx === idx); });
    if (scroll) {
      var row = box.querySelector('[data-idx="' + idx + '"]');
      if (row) row.scrollIntoView({ block: "nearest" });
    }
    var panel = document.getElementById("io-panel");
    panel.innerHTML = '<p class="muted">Loading q#' + Lab.esc(V.entries[idx] && V.entries[idx].qid) + "… (an old run's prompt is rebuilt from git: first time takes a moment)</p>";
    Lab.get("/api/question", { run: V.run, idx: idx }).then(function (res) {
      if (V.selected !== idx) return;
      V.lastIo = res;
      V.renderIo(res);
    }).catch(function (e) { panel.innerHTML = '<p class="pill err">' + Lab.esc(e.message) + "</p>"; });
  };

  function pretty(v) {
    if (v === null || v === undefined) return "";
    if (typeof v === "string") return v;
    return JSON.stringify(v, null, 2);
  }

  /* Playground: edit the prompt and the question, ask Jev and Laya at once, answers side
     by side next to what was logged.  Drafts + answers are kept per question while the page is open. */
  V.askCache = {};
  function questionsOf(req) {
    if (!req || typeof req !== "object") return null;
    return req.questions || (req.response_format && req.response_format.questions) || null;
  }
  V.askTab = function (e, io, out) {
    var key = V.run + "#" + V.selected;
    var orig = { prompt: io.prompt || "", questions: JSON.stringify(questionsOf(io.request) || {}, null, 2) };
    var c = V.askCache[key] = V.askCache[key] || { draft: { prompt: orig.prompt, questions: orig.questions } };
    out.innerHTML =
      '<p class="small muted">Edit the prompt and/or the question, then ask. Logged choice: <code>' + Lab.esc(e.choice || "no answer") +
      '</code>. Nothing here is saved to files. First Laya ask loads the model (~40 s).</p>' +
      '<label class="small muted">Prompt <span class="js-ed-p"></span></label><textarea class="play-ta js-p" rows="14" spellcheck="false"></textarea>' +
      '<label class="small muted">Question (instructions, options + criteria) <span class="js-ed-q"></span></label><textarea class="play-ta js-q" rows="10" spellcheck="false"></textarea>' +
      '<p><button class="btn js-ask-both">Ask Jev + Laya</button> <button class="btn tiny js-reset">Reset to logged</button> <span class="js-err"></span></p>' +
      '<div class="ask-grid"><div class="js-ask-jev"></div><div class="js-ask-laya"></div></div>';
    var ta = out.querySelector(".js-p"), tq = out.querySelector(".js-q");
    ta.value = c.draft.prompt; tq.value = c.draft.questions;
    function marks() {
      out.querySelector(".js-ed-p").innerHTML = ta.value !== orig.prompt ? '<span class="pill warn">edited</span>' : "";
      out.querySelector(".js-ed-q").innerHTML = tq.value !== orig.questions ? '<span class="pill warn">edited</span>' : "";
    }
    ta.addEventListener("input", function () { c.draft.prompt = ta.value; marks(); });
    tq.addEventListener("input", function () { c.draft.questions = tq.value; marks(); });
    marks();
    function card(model, st) {
      var name = model === "jev" ? "Jev" : "Laya", el = out.querySelector(".js-ask-" + model);
      if (!el) return;
      if (!st) { el.innerHTML = '<div class="ask-card"><b>' + name + '</b><p class="muted small">not asked yet</p></div>'; return; }
      if (st.pending) { el.innerHTML = '<div class="ask-card"><b>' + name + '</b><p class="muted small">asking…</p></div>'; return; }
      var r = st.r;
      if (!r.ok) { el.innerHTML = '<div class="ask-card"><b>' + name + '</b><p class="pill err">' + Lab.esc(r.error) + "</p></div>"; return; }
      var probs = r.probabilities || {};
      var bars = Object.keys(probs).sort(function (x, y) { return probs[y] - probs[x]; }).map(function (k) {
        return '<span class="name">' + Lab.esc(k) + "</span>" +
          '<div><div class="bar' + (k === r.choice ? " chosen" : "") + '" style="width:' + Math.max(1, probs[k] * 100) + '%"></div></div>' +
          "<span>" + Math.round(probs[k] * 100) + "%</span>";
      }).join("");
      var same = r.choice === e.choice;
      el.innerHTML = '<div class="ask-card"><b>' + name + ":</b> <code>" + Lab.esc(r.choice) + "</code> " +
        '<span class="pill ' + (same ? "ok" : "warn") + '">' + (same ? "same as logged" : "differs from logged") + "</span>" +
        (st.edited ? ' <span class="pill">with your edits</span>' : "") +
        '<div class="muted small">' + Lab.esc(r.model || "") + " · " + r.ms + " ms" + (r.load_s ? " · model load " + r.load_s + " s" : "") + "</div>" +
        (bars ? '<div class="probs">' + bars + "</div>" : "") +
        '<details><summary class="small muted">raw reply</summary><pre class="block">' + Lab.esc(r.raw || "") + "</pre></details></div>";
    }
    function draw() { card("jev", c.jev); card("laya", c.laya); }
    draw();
    out.querySelector(".js-reset").addEventListener("click", function () {
      ta.value = c.draft.prompt = orig.prompt; tq.value = c.draft.questions = orig.questions; marks();
    });
    out.querySelector(".js-ask-both").addEventListener("click", function () {
      var err = out.querySelector(".js-err"), qs;
      err.innerHTML = "";
      try { qs = JSON.parse(tq.value); } catch (x) { err.innerHTML = '<span class="pill err">Question is not valid JSON: ' + Lab.esc(x.message) + "</span>"; return; }
      var edited = ta.value !== orig.prompt || tq.value !== orig.questions;
      ["jev", "laya"].forEach(function (model) {
        c[model] = { pending: true };
        Lab.post("/api/ask", { run: V.run, idx: V.selected, model: model, prompt: ta.value, questions: qs }).then(function (res) {
          c[model] = { r: res.result, edited: edited };
        }).catch(function (x) {
          c[model] = { r: { ok: false, error: x.message } };
        }).then(function () { if (V.run + "#" + V.selected === key && V.ioTab === "ask") card(model, c[model]); });
      });
      draw();
    });
  };

  V.renderIo = function (res) {
    var e = res.entry, io = res.io || {}, a = e.game.applied || {};
    var modePill = io.mode === "exact" ? '<span class="pill ok">exact prompt (logged)</span>' :
      io.mode === "rebuilt" ? '<span class="pill warn">rebuilt at ' + Lab.esc(io.sha) + "</span>" :
      io.mode === "rebuilt from current files" ? '<span class="pill err">rebuilt from current files</span>' :
      '<span class="pill err">' + Lab.esc(io.mode || "no prompt") + "</span>";
    var probs = e.probs || {};
    var used = e.game.choice || e.choice;
    var probHtml = Object.keys(probs).length ? '<div class="probs">' + Object.keys(probs).sort(function (x, y) { return probs[y] - probs[x]; }).map(function (k) {
      return '<span class="name" title="' + Lab.esc(k) + '">' + Lab.esc(k) + (k === e.rule ? ' <span class="muted">(rule)</span>' : "") + "</span>" +
        '<div><div class="bar' + (k === used ? " chosen" : "") + '" style="width:' + Math.max(1, probs[k] * 100) + '%"></div></div>' +
        "<span>" + Math.round(probs[k] * 100) + "%</span>";
    }).join("") + "</div>" : "";
    var trig = (e.trigger || []).map(function (p) {
      return "<code>" + Lab.esc(p.part) + "</code>: " + p.events.map(function (x) { return Lab.esc(x.text); }).join("; ");
    }).join("<br>");
    var h = [];
    h.push('<div class="io-head"><h3>q#' + e.qid + " · " + Lab.esc(e.kind) + "</h3>" +
      '<span class="muted">minute ' + Lab.esc(e.minute) + " · " + Lab.esc(V.seat(e.player)) + "</span>" + modePill +
      "</div>");
    h.push('<dl class="kv">' +
      "<dt>Why asked</dt><dd>" + (trig || "-") + "</dd>" +
      (e.event_chain ? "<dt>Chain</dt><dd><code>" + Lab.esc(e.event_chain.id) + "</code> " + Lab.esc(e.event_chain.event || "") +
        (e.event_chain.persona ? " → " + Lab.esc(e.event_chain.persona) : "") +
        (relevanceQid(e.event_chain) ? " (Relevance Router q#" + relevanceQid(e.event_chain) + ")" : "") +
        (e.event_chain.sent ? " (after our q#" + e.event_chain.sent.join(", q#") + " entered Petra's queues)" : "") +
        (questionQid(e.event_chain) ? " → " + Lab.esc(e.kind) + " (Question Router q#" + questionQid(e.event_chain) + ")" : "") +
        (e.event_chain.router ? " · " + routerName(e.event_chain.router) + (e.event_chain.leaf ? " for " + Lab.esc(e.event_chain.leaf) : "") : "") +
        "</dd>" : "") +
      "<dt>Offered</dt><dd>" + e.options.map(function (o) { return '<span class="opt' + (o === used ? " chosen" : "") + (o === e.rule ? " rule" : "") + '">' + Lab.esc(o) + "</span>"; }).join("") + "</dd>" +
      "<dt>Rule's pick</dt><dd><code>" + Lab.esc(e.rule) + "</code> <span class=\"muted small\">(the default if no answer comes in time)</span></dd>" +
      "<dt>Model</dt><dd>" + (e.choice ? "<code>" + Lab.esc(e.choice) + "</code>" + (e.p !== null && e.p !== undefined ? " p=" + e.p : "") : '<span class="muted">no answer</span>') +
        ' <span class="muted small">' + Lab.esc(e.adapter || "") + " · " + Lab.esc(e.outcome || "") + (e.latency_ms ? " · " + Math.round(e.latency_ms) + " ms" : "") + "</span></dd>" +
      "<dt>Game used</dt><dd>" + (e.game.choice ? "<code>" + Lab.esc(e.game.choice) + "</code> by " + Lab.esc(e.game.by) : '<span class="muted">not in engine.log</span>') + "</dd>" +
      "<dt>Petra did</dt><dd>" + Lab.esc(a.text || "-") + (a.line ? ' <span class="muted small">(engine.log:' + a.line + ")</span>" : "") + "</dd>" +
      '<dt>Logged at</dt><dd class="small muted">advisor/' + Lab.esc(e.source.file) + ":" + Lab.esc(e.source.line) + "</dd></dl>");
    h.push(probHtml);
    h.push('<div class="io-tabs">' + ["prompt", "request", "reply", "petra", "ask"].map(function (t) {
      var label = { prompt: "Prompt", request: "Request", reply: "Raw reply", petra: "Petra (engine.log)", ask: "Try it (edit + ask)" }[t];
      return '<button data-t="' + t + '"' + (t === V.ioTab ? ' class="on"' : "") + ">" + label + "</button>";
    }).join("") + '</div><div class="js-io"></div>');
    var panel = document.getElementById("io-panel");
    panel.innerHTML = h.join("");
    function tab(t) {
      V.ioTab = t;
      panel.querySelectorAll(".io-tabs button").forEach(function (b) { b.classList.toggle("on", b.dataset.t === t); });
      var out = panel.querySelector(".js-io");
      var notes = (io.notes || []).length ? '<ul class="small muted">' + io.notes.map(function (n) { return "<li>" + Lab.esc(n) + "</li>"; }).join("") + "</ul>" : "";
      if (t === "ask") {
        V.askTab(e, io, out);
      } else if (t === "prompt") {
        out.innerHTML = notes + (io.ok === false ? '<p class="pill err">' + Lab.esc(io.error) + "</p>" : "") +
          '<pre class="block">' + Lab.esc(io.prompt || "(no prompt)") + "</pre>";
      } else if (t === "request") {
        out.innerHTML = '<p class="small muted">' + (io.mode === "exact" ? "As logged (io.request)." :
          "Rebuilt: the body advisor_jev._post sends (the api-key header is never shown or logged).") + "</p>" +
          '<pre class="block">' + Lab.esc(pretty(io.request)) + "</pre>";
      } else if (t === "reply") {
        out.innerHTML = io.mode === "exact" ?
          '<pre class="block">' + Lab.esc(io.raw_reply_parsed ? JSON.stringify(io.raw_reply_parsed, null, 2) : io.raw_reply || "(empty)") + "</pre>" :
          '<p class="muted">' + Lab.esc(io.raw_reply_note || "raw reply not logged") + "</p>" +
          '<pre class="block">' + Lab.esc(JSON.stringify({ choice: e.choice, p: e.p, probabilities: e.probs, outcome: e.outcome, error: e.error }, null, 2)) + "</pre>";
      } else {
        var g = e.game, lines = [];
        if (g.asked) lines.push({ line: g.asked.line, text: g.asked.text, what: "asked" });
        if (g.settled) lines.push({ line: g.settled.line, text: g.settled.text, what: "settled" });
        (g.lines || []).forEach(function (l) { lines.push({ line: l.line, text: l.text, what: "order" }); });
        lines.sort(function (x, y) { return x.line - y.line; });
        out.innerHTML = '<p class="small muted">engine.log lines for q#' + e.qid + " (" + Lab.esc(V.seat(e.player)) + ")</p>" +
          (lines.length ? '<div class="snippet">' + lines.map(function (l) {
            return '<div class="ln"><span class="n">' + l.line + "</span>" + Lab.esc(l.text) + "</div>";
          }).join("") + "</div>" : '<p class="muted">none</p>');
      }
    }
    panel.querySelectorAll(".io-tabs button").forEach(function (b) {
      b.addEventListener("click", function () { tab(b.dataset.t); });
    });
    tab(V.ioTab);
  };

  V.poll = function (force) {
    if (Lab.state.tab !== "game" && !force) return;
    if (V.live || (V.runInfo && V.runInfo.live)) V.fetch(!!V.run && !force);
  };

  V.startPolling = function () {
    if (V.timer) return;
    V.timer = setInterval(function () {
      V.poll(false);
      V.status();
    }, 2000);
    setInterval(function () { if (Lab.state.tab === "game") V.loadRuns(); }, 15000);
  };
})();
