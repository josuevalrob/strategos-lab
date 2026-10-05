/* The Pipe view: one lane per block file, read from the 0 A.D. files (/api/pipe), pushed live
   (/api/pipe/events, SSE).  Plain SVG, fixed columns:
     facts -> filters -> context steps -> wording -> model -> answer
     -> actor track (bottom, right to left) -> acks -> back into the lane's "pending" filter.
   Shared nodes (readers, the common first context steps, models, actor) are drawn once. */
"use strict";

(function () {
  var V = Lab.views.pipe = { data: null, rev: 0, es: null, sel: null, nodes: {}, sigs: null, dirty: false };
  var C = { pad: 16, padR: 40, gap: 30, facts: 104, filters: 184, question: 210, model: 96, answer: 156,
            chipH: 38, rowGap: 14, chipGap: 16, laneHead: 26, lanePad: 10, laneGap: 12, rowH: 15,
            trackGap: 50, actH: 46 };
  var F_T = "600 12px -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
  var F_S = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
  var F_LT = "600 13px -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
  var F_TINY = "10px -apple-system, BlinkMacSystemFont, sans-serif";
  var mctx = null;

  function tw(text, font) {
    if (!mctx) mctx = document.createElement("canvas").getContext("2d");
    mctx.font = font || F_T;
    return mctx.measureText(String(text)).width;
  }
  function fit(text, font, w) {
    text = String(text === undefined || text === null ? "" : text);
    if (tw(text, font) <= w) return text;
    while (text.length > 1 && tw(text + "…", font) > w) text = text.slice(0, -1);
    return text + "…";
  }
  function wrap(text, font, w, maxLines) {
    var words = String(text || "").split(/\s+/), lines = [], cur = "";
    words.forEach(function (wd) {
      var t = cur ? cur + " " + wd : wd;
      if (tw(t, font) > w && cur) { lines.push(cur); cur = wd; } else cur = t;
    });
    if (cur) lines.push(cur);
    if (lines.length > maxLines) { lines = lines.slice(0, maxLines); lines[maxLines - 1] = fit(lines[maxLines - 1] + " …", font, w); }
    return lines;
  }
  var E = Lab.esc;
  function lc(li) { return "l" + (li % 6); }

  /* -- data in --------------------------------------------------------------- */
  V.init = function () {
    var host = document.getElementById("pipe-canvas");
    host.addEventListener("click", function (ev) {
      var g = ev.target.closest("[data-key]");
      if (g) V.select(g.getAttribute("data-key"));
    });
    document.getElementById("pipe-overlay").addEventListener("change", function () { V.render(); });
    var t = null;
    window.addEventListener("resize", function () { clearTimeout(t); t = setTimeout(V.render, 120); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && V.sel) V.select(null); });
    V.connect();
  };

  V.connect = function () {
    if (V.es) V.es.close();
    if (!window.EventSource) { V.poll(); return; }
    V.es = new EventSource("/api/pipe/events?rev=" + V.rev);
    V.es.onmessage = function (m) {
      try { V.take(JSON.parse(m.data)); } catch (e) { Lab.toast("Pipe: " + e.message, true); }
    };
    V.es.onerror = function () { V.status("reconnecting…"); };
  };
  V.poll = function () {
    Lab.get("/api/pipe").then(V.take).catch(function () { /* next round */ });
    setTimeout(V.poll, 1500);
  };

  V.take = function (d) {
    if (!d || d.rev === V.rev) return;
    V.rev = d.rev;
    V.data = d;
    V.render();
  };

  V.shown = function () { if (V.dirty || !V.drawn) V.render(); };
  V.onTheme = function () { /* colours are CSS variables */ };

  V.status = function (extra) {
    var d = V.data, ov = d && d.overlay;
    var s = d ? "rev " + d.rev + " · read " + new Date(d.built_at * 1000).toLocaleTimeString() : "loading…";
    if (ov && ov.run) s += " · run " + ov.run + (ov.live ? " (live)" : "");
    if (extra) s += " · " + extra;
    document.getElementById("pipe-status").textContent = s;
  };

  /* -- layout + draw ------------------------------------------------------------ */
  function chipW(st) {
    var w = tw(st.label, F_T) + 30;
    if (st.params && st.params.length) w = Math.max(w, tw(st.params.join(", "), F_S) + 18);
    return Math.max(70, Math.min(190, Math.ceil(w)));
  }

  V.render = function () {
    var d = V.data;
    var host = document.getElementById("pipe-canvas");
    if (!d) return;
    if (!host.offsetParent) { V.dirty = true; return; }
    V.dirty = false;
    V.drawn = true;
    V.status();
    var errs = (d.errors || []);
    var eb = document.getElementById("pipe-errors");
    eb.innerHTML = errs.map(function (e) { return "<div>" + E(e) + "</div>"; }).join("");
    eb.classList.toggle("hidden", !errs.length);

    var useOv = document.getElementById("pipe-overlay").checked;
    var ovl = (useOv && d.overlay && d.overlay.lanes) || {};
    var W = Math.max(host.clientWidth, 1200);
    var lanes = d.lanes || [], T = d.trunk || 0, nl = lanes.length;
    var N = {}, out = [], bands = [], edges = [], overs = [];
    function off(li) { return (li - (nl - 1) / 2) * 5; }
    function node(key, n) { n.key = key; N[key] = n; return n; }

    var xFacts = C.pad, xFil = xFacts + C.facts + C.gap, xCtx = xFil + C.filters + C.gap;
    var xAns = W - C.padR - C.answer, xMod = xAns - C.gap - C.model, xQ = xMod - C.gap - C.question;
    var ctxRight = xQ - C.gap;

    // Shared context steps: the first T steps every lane with a context list has.
    var withCtx = lanes.filter(function (l) { return l.context && l.context.length; });
    var trunk = [], x = xCtx;
    for (var i = 0; i < T && withCtx.length; i++) {
      var st = withCtx[0].context[i], w = chipW(st);
      trunk.push({ st: st, x: x, w: w });
      x += w + C.chipGap;
    }
    var xOwn = T ? x - C.chipGap + C.gap : xCtx;
    var rules = (d.rules || []).concat([d.pending]);

    // Lanes: rows of own context chips, wrapped at the wording column.
    var y = C.pad;
    lanes.forEach(function (ln, li) {
      var own = ln.context ? ln.context.slice(ln.context.length >= T && withCtx.indexOf(ln) >= 0 ? T : 0) : [];
      var rows = [[]], cx = xOwn;
      own.forEach(function (st) {
        var w = chipW(st);
        if (rows[rows.length - 1].length && cx + w > ctxRight) { rows.push([]); cx = xOwn; }
        rows[rows.length - 1].push({ st: st, x: cx, w: w });
        cx += w + C.chipGap;
      });
      if (!rows[0].length) rows = [];
      var ctxH = rows.length ? rows.length * C.chipH + (rows.length - 1) * C.rowGap : 0;
      var filH = ln.filters ? 34 + rules.length * C.rowH + 20 : 40;
      var inner = Math.max(ctxH, filH, 96, 84);
      var h = C.laneHead + inner + 2 * C.lanePad;
      ln._g = { y: y, h: h, top: y + C.laneHead + C.lanePad, rows: rows };
      ln._g.main = ln._g.top + C.chipH / 2;
      y += h + C.laneGap;
    });
    var lanesBottom = y - C.laneGap;
    var mains = lanes.map(function (l) { return l._g.main; });
    var midY = mains.length ? (Math.min.apply(null, mains) + Math.max.apply(null, mains)) / 2 : C.pad + 60;

    // Shared: readers.
    var rd = d.readers || [], rH = 40, rGap = 18;
    var rTop = midY - (rd.length * rH + (rd.length - 1) * rGap) / 2;
    rd.forEach(function (r, i) {
      node(r.id, { x: xFacts, y: rTop + i * (rH + rGap), w: C.facts, h: rH, cls: "c-facts shared", title: r.label,
                   sub: "reader", ref: r, kind: "reader", data: r });
    });
    // Shared: trunk steps.
    trunk.forEach(function (t, i) {
      node("trunk:" + i, { x: t.x, y: midY - C.chipH / 2, w: t.w, h: C.chipH, cls: "c-ctx shared", title: t.st.label,
                           sub: (t.st.params || []).join(", "), num: t.st.n, err: t.st.error, kind: "step", data: t.st,
                           ref: t.st, lane: null });
    });
    // Shared: models.
    var ms = ((d.models || {}).models) || [];
    var mH = 26 + ms.length * 20;
    node("models", { x: xMod, y: midY - mH / 2, w: C.model, h: mH, cls: "c-model shared", title: "model",
                     kind: "models", data: d.models, ref: (d.models || {}).registry, rows: ms });
    // Lanes.
    lanes.forEach(function (ln, li) {
      var g = ln._g, o = ovl[ln.id], L = lc(li);
      bands.push('<rect class="pp-band ' + (li % 2 ? "odd" : "") + '" x="0" y="' + g.y + '" width="' + W + '" height="' + g.h + '"/>');
      var lsText = [ln.building, ln.kind, ln.path ? ln.path.split("/").pop() : null].filter(Boolean).join(" · ") +
        (ln.context ? "" : " · no context list yet");
      var hx = xFil, lx = hx + 22 + tw(ln.id, F_LT);
      var head = '<g class="pp-lanehead" data-key="lane:' + E(ln.id) + '"><circle class="' + L + ' dot" cx="' + (hx + 5) + '" cy="' + (g.y + 14) + '" r="5"/>' +
        '<text class="lt" x="' + (hx + 16) + '" y="' + (g.y + 18) + '">' + E(ln.id) + '</text>' +
        '<text class="ls" x="' + lx + '" y="' + (g.y + 18) + '">' + E(lsText) + "</text>" +
        (ln.errors && ln.errors.length ? '<text class="lerr" x="' + (lx + tw(lsText, F_S) + 16) + '" y="' + (g.y + 18) + '">⚠ ' +
          E(fit(ln.errors[0], F_S, 520)) + (ln.errors.length > 1 ? " (+" + (ln.errors.length - 1) + ")" : "") + "</text>" : "") +
        "</g>";
      bands.push(head);

      // Filters box.
      var fk = "filters:" + ln.id;
      var fil = node(fk, { x: xFil, y: g.top, w: C.filters, h: ln.filters ? 34 + rules.length * C.rowH + 14 : 38,
                           cls: "c-filter", title: ln.filters ? "option filters" : "no filters (kind has none)", lane: li,
                           kind: "filters", data: ln, ov: o, ref: (ln.kind_ref || {}).options });
      if (ln.filters) {
        fil.rows = rules.map(function (r, ri) {
          var hit = [];
          if (o && o.dropped) Object.keys(o.dropped).forEach(function (line) {
            if (o.dropped[line].rule === r.label) hit.push(line);
          });
          return { key: "rule:" + r.label + "@" + ln.id, label: r.label, hit: hit, err: r.error, pending: r.label === "pending",
                   y: g.top + 34 + ri * C.rowH };
        });
        fil.rows.forEach(function (row, ri) {
          node(row.key, { x: xFil, y: row.y - 11, w: C.filters, h: C.rowH, row: true, lane: li, kind: "rule",
                          data: rules[ri], ov: o, ln: ln, ref: rules[ri], title: rules[ri].label });
        });
        fil.pendingY = fil.rows[fil.rows.length - 1].y - 4;
      } else fil.pendingY = g.top + 26;

      // Own context chips.
      var chips = [];
      g.rows.forEach(function (row, ri) {
        row.forEach(function (c) {
          chips.push(node("ctx:" + ln.id + ":" + c.st.n, {
            x: c.x, y: g.top + ri * (C.chipH + C.rowGap), w: c.w, h: C.chipH, cls: "c-ctx", title: c.st.label,
            sub: (c.st.params || []).join(", "), num: c.st.n, err: c.st.error, lane: li, kind: "step",
            data: c.st, ln: ln, ref: c.st, row: false, rowIndex: ri }));
        });
      });

      // Wording.
      var q = ln.question || {};
      var qn = node("q:" + ln.id, { x: xQ, y: g.top, w: C.question, h: 84, cls: "c-question", title: ln.id, lane: li,
                                    kind: "question", data: ln, ref: q,
                                    lines: wrap(q.instructions || "(no wording)", F_S, C.question - 16, 2),
                                    crit: Object.keys(q.criteria || {}) });
      // Answer (+ overlay).
      var an = node("ans:" + ln.id, { x: xAns, y: g.top, w: C.answer, h: 84, cls: "c-answer", title: "answer", lane: li,
                                      kind: "answer", data: ln, ov: o, ref: d.answer });

      // Lane edges.
      (ln.domains || []).forEach(function (dom) {
        var r = N["reader:" + dom];
        if (r) edges.push({ L: L, d: hpath(r.x + r.w, r.y + r.h / 2 + off(li), fil.x, g.main) });
      });
      var prev = { x: fil.x + fil.w, y: g.main };
      var laneTrunk = ln.context && withCtx.indexOf(ln) >= 0 && T > 0;
      if (laneTrunk) {
        trunk.forEach(function (t, i) {
          var tn = N["trunk:" + i], ty = tn.y + tn.h / 2 + off(li);
          edges.push({ L: L, d: hpath(prev.x, prev.y, tn.x, ty) });
          prev = { x: tn.x + tn.w, y: ty };
        });
      }
      chips.forEach(function (c, i) {
        var cy = c.y + c.h / 2;
        if (i > 0 && c.rowIndex !== chips[i - 1].rowIndex) {
          // wrap: right of the row's last chip, down between rows, back left, into the next row
          var gy = c.y - C.rowGap / 2, gx1 = prev.x + 8, gx2 = c.x - 8;
          edges.push({ L: L, d: "M" + prev.x + "," + prev.y + " H" + gx1 + " V" + gy + " H" + gx2 + " V" + cy + " H" + c.x, wrap: true });
        } else edges.push({ L: L, d: hpath(prev.x, prev.y, c.x, cy) });
        prev = { x: c.x + c.w, y: cy };
      });
      edges.push({ L: L, d: hpath(prev.x, prev.y, qn.x, g.main) });
      var mn = N.models;
      edges.push({ L: L, d: hpath(qn.x + qn.w, g.main, mn.x, mn.y + mn.h / 2 + off(li)) });
      edges.push({ L: L, d: hpath(mn.x + mn.w, mn.y + mn.h / 2 + off(li), an.x, g.main) });
      ln._an = an; ln._fil = fil;
    });

    // Actor track at the bottom, right to left, ending in the acks.
    var acts = d.actor || [];
    var tY = lanesBottom + C.trackGap, xR = xAns + C.answer / 2, xL = xFil + C.filters / 2;
    var step = acts.length > 1 ? (xR - xL) / (acts.length - 1) : 0;
    var aw = Math.min(170, Math.max(120, step - 22));
    acts.forEach(function (a, i) {
      var cxp = xR - i * step;
      node(a.id, { x: cxp - aw / 2, y: tY, w: aw, h: C.actH, cls: "c-actor shared", title: a.label, sub: a.sub,
                   err: a.error, kind: "actor", data: a, ref: a });
    });
    var trackCy = tY + C.actH / 2;
    acts.forEach(function (a, i) {
      if (!i) return;
      var from = N[acts[i - 1].id], to = N[a.id];
      edges.push({ L: "track", d: "M" + from.x + "," + trackCy + " H" + (to.x + to.w + 6), arrowL: { x: to.x + to.w, y: trackCy } });
    });
    if (acts.length) {
      var send = N[acts[0].id], acks = N[acts[acts.length - 1].id];
      lanes.forEach(function (ln, li) {
        var L = lc(li), an = ln._an, fil = ln._fil;
        var gx = an.x + an.w + 10 + li * 6, sy = trackCy + off(li);
        edges.push({ L: L, d: "M" + (an.x + an.w) + "," + ln._g.main + " H" + gx + " V" + sy + " H" + (send.x + send.w + 6),
                     arrowL: { x: send.x + send.w, y: sy } });
        var ax = acks.x + 14 + li * 7, gl = xFil - 8 - li * 6, up = tY - 12 - li * 6;
        edges.push({ L: L, d: "M" + ax + "," + tY + " V" + up + " H" + gl + " V" + fil.pendingY + " H" + (fil.x - 6),
                     arrowR: { x: fil.x, y: fil.pendingY }, back: true });
      });
    }
    var H = tY + C.actH + C.pad + 18;

    // -- draw -------------------------------------------------------------------
    var svg = ['<svg class="pipe-svg" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + " " + H + '">'];
    svg.push(bands.join(""));
    svg.push('<text class="pp-col" x="' + xFacts + '" y="' + (H - 8) + '">game facts</text>' +
      '<text class="pp-col" x="' + xFil + '" y="' + (H - 8) + '">filters + pending</text>' +
      '<text class="pp-col" x="' + xCtx + '" y="' + (H - 8) + '">context steps (block "context" list, in order)</text>' +
      '<text class="pp-col" x="' + xQ + '" y="' + (H - 8) + '">wording</text>' +
      '<text class="pp-col" x="' + xMod + '" y="' + (H - 8) + '">model</text>' +
      '<text class="pp-col" x="' + xAns + '" y="' + (H - 8) + '">answer</text>' +
      '<text class="pp-col" x="' + (xL + 40) + '" y="' + (tY - 18) + '">actor: answer → game → Petra → acks → pending ←</text>');
    edges.forEach(function (e) {
      svg.push('<path class="pp-edge ' + e.L + (e.wrap ? " wrap" : "") + (e.back ? " back" : "") + '" d="' + e.d + '"/>');
      if (e.arrowL) svg.push('<path class="pp-arrow ' + e.L + '" d="M' + e.arrowL.x + "," + e.arrowL.y + " l7,-4 v8 z" + '"/>');
      if (e.arrowR) svg.push('<path class="pp-arrow ' + e.L + '" d="M' + e.arrowR.x + "," + e.arrowR.y + " l-7,-4 v8 z" + '"/>');
    });
    Object.keys(N).forEach(function (k) { if (!N[k].row) svg.push(draw(N[k])); });
    svg.push("</svg>");
    host.innerHTML = svg.join("");
    V.nodes = N;

    // Flash what changed since the last build.
    var sigs = {};
    Object.keys(N).forEach(function (k) {
      var n = N[k];
      sigs[k] = JSON.stringify([n.title, n.sub, n.err, n.num, n.ref && n.ref.line, n.lines, n.crit]);
    });
    if (V.sigs) Object.keys(sigs).forEach(function (k) {
      if (V.sigs[k] !== sigs[k]) {
        var el = host.querySelector('[data-key="' + cssEsc(k) + '"]');
        if (el) el.classList.add("flash");
      }
    });
    V.sigs = sigs;
    if (V.sel && N[V.sel]) V.select(V.sel, true); else if (V.sel) V.select(null);
  };

  function cssEsc(s) { return window.CSS && CSS.escape ? CSS.escape(s) : s.replace(/"/g, '\\"'); }

  function hpath(x1, y1, x2, y2) {
    var dx = Math.max(16, (x2 - x1) / 2);
    return "M" + x1 + "," + y1 + " C" + (x1 + dx) + "," + y1 + " " + (x2 - dx) + "," + y2 + " " + x2 + "," + y2;
  }

  function draw(n) {
    var cls = "pp-node " + (n.cls || "") + (n.err ? " err" : "") + (V.sel === n.key ? " sel" : "");
    var h = ['<g class="' + cls + '" data-key="' + E(n.key) + '">'];
    h.push('<rect x="' + n.x + '" y="' + n.y + '" width="' + n.w + '" height="' + n.h + '" rx="7"/>');
    var tx = n.x + 9, ty = n.y + 16;
    if (n.num) {
      h.push('<circle class="num" cx="' + (n.x + 12) + '" cy="' + (n.y + 12) + '" r="7"/><text class="numt" x="' + (n.x + 12) + '" y="' + (n.y + 15.5) + '">' + n.num + "</text>");
      tx = n.x + 24;
    }
    if (n.err) h.push('<text class="errmark" x="' + (n.x + n.w - 12) + '" y="' + (n.y + 15) + '">!</text>');
    h.push('<text class="t" x="' + tx + '" y="' + ty + '">' + E(fit(n.title, F_T, n.x + n.w - tx - (n.err ? 16 : 6))) + "</text>");
    if (n.sub) h.push('<text class="s" x="' + (n.x + 9) + '" y="' + (n.y + 31) + '">' + E(fit(n.sub, F_S, n.w - 14)) + "</text>");

    if (n.kind === "models") {
      (n.rows || []).forEach(function (m, i) {
        var ry = n.y + 24 + i * 20;
        h.push('<g class="pp-row' + (V.sel === m.id ? " sel" : "") + '" data-key="' + E(m.id) + '"><rect x="' + (n.x + 4) + '" y="' + ry + '" width="' + (n.w - 8) + '" height="18" rx="4"/>' +
          '<text class="s" x="' + (n.x + 10) + '" y="' + (ry + 13) + '">' + E(fit(m.label, F_S, n.w - 20)) + "</text></g>");
      });
    }
    if (n.kind === "filters" && n.rows) {
      n.rows.forEach(function (r) {
        var bad = r.hit.length;
        h.push('<g class="pp-row' + (r.pending ? " pend" : "") + (bad ? " hit" : "") + (r.err ? " rerr" : "") + (V.sel === r.key ? " sel" : "") + '" data-key="' + E(r.key) + '">' +
          '<rect x="' + (n.x + 4) + '" y="' + (r.y - 11) + '" width="' + (n.w - 8) + '" height="' + (C.rowH - 1) + '" rx="3"/>' +
          '<text class="s" x="' + (n.x + 10) + '" y="' + r.y + '">' + E(r.label) + "</text>" +
          (bad ? '<text class="s drop" text-anchor="end" x="' + (n.x + n.w - 8) + '" y="' + r.y + '">✗ ' + E(fit(r.hit.join(", "), F_S, n.w - tw(r.label, F_S) - 34)) + "</text>" : "") +
          "</g>");
      });
      var o = n.ov;
      if (o && o.offered) {
        h.push('<text class="s okt" x="' + (n.x + 10) + '" y="' + (n.y + n.h - 5) + '">' +
          E(fit("✓ offered: " + (o.offered.join(", ") || "none") + (o.minute !== undefined ? "  (m" + o.minute + ")" : ""), F_S, n.w - 16)) + "</text>");
      }
    }
    if (n.kind === "question") {
      (n.lines || []).forEach(function (l, i) {
        h.push('<text class="s q" x="' + (n.x + 9) + '" y="' + (n.y + 33 + i * 14) + '">' + E(l) + "</text>");
      });
      h.push('<text class="s crit" x="' + (n.x + 9) + '" y="' + (n.y + 76) + '">' + E(fit(n.crit.join(" · ") || "(no criteria)", F_S, n.w - 16)) + "</text>");
    }
    if (n.kind === "answer") {
      var a = n.ov;
      if (!a || !a.minute && a.minute !== 0) {
        h.push('<text class="s" x="' + (n.x + 9) + '" y="' + (n.y + 34) + '">' + E(fit("none in the newest run", F_S, n.w - 16)) + "</text>");
      } else {
        h.push('<text class="big" x="' + (n.x + 9) + '" y="' + (n.y + 38) + '">' + E(fit(a.choice || (a.error ? "error" : "-"), F_T, n.w - 18)) + "</text>");
        h.push('<text class="s" x="' + (n.x + 9) + '" y="' + (n.y + 54) + '">' + E(fit("m" + a.minute + " · " + (a.model || "?") + " · " + (a.outcome || "-"), F_S, n.w - 16)) + "</text>");
        var states = ["applied", "started", "finished"], got = {};
        (a.acks || []).forEach(function (k) { got[k.state] = true; });
        var bad = got.dropped ? "dropped" : got.rejected ? "rejected" : null;
        var cx = n.x + 13;
        states.forEach(function (s) {
          h.push('<circle class="ack' + (got[s] ? " on" : "") + '" cx="' + cx + '" cy="' + (n.y + 69) + '" r="4"/>' +
            '<text class="tiny" x="' + (cx + 7) + '" y="' + (n.y + 72) + '">' + s + "</text>");
          cx += tw(s, F_TINY) + 20;
        });
        if (bad) h.push('<text class="s drop" x="' + (n.x + 9) + '" y="' + (n.y + 82) + '">' + bad + "</text>");
      }
    }
    h.push("</g>");
    return h.join("");
  }

  /* -- side panel ------------------------------------------------------------------- */
  V.select = function (key, keep) {
    var panel = document.getElementById("pipe-panel");
    var host = document.getElementById("pipe-canvas");
    host.querySelectorAll(".sel").forEach(function (e) { e.classList.remove("sel"); });
    if (!key || (!V.nodes[key] && key.indexOf("lane:") !== 0)) {
      V.sel = null;
      panel.classList.add("hidden");
      return;
    }
    V.sel = key;
    var el = host.querySelector('[data-key="' + cssEsc(key) + '"]');
    if (el) el.classList.add("sel");
    var scroll = keep ? panel.scrollTop : 0;
    panel.innerHTML = '<button class="btn ghost tiny pp-close" title="Close (Esc)">✕</button>' + V.detail(key);
    panel.classList.remove("hidden");
    panel.scrollTop = scroll;
    panel.querySelector(".pp-close").addEventListener("click", function () { V.select(null); });
    panel.querySelectorAll(".js-edit").forEach(function (b) {
      b.addEventListener("click", function () { Lab.show("edit", { path: b.dataset.path }); });
    });
    var hit = panel.querySelector(".ln.hit");
    if (hit && !keep) hit.scrollIntoView({ block: "nearest" });
  };

  function where(ref) {
    if (!ref || !ref.path) return "";
    var h = '<div class="pp-where mono">' + E(ref.path) + (ref.line ? ":" + ref.line : "") + "</div>";
    if (ref.snippet && ref.snippet.length) {
      h += '<div class="snippet">' + ref.snippet.map(function (l) {
        return '<div class="ln' + (l[0] === ref.line ? " hit" : "") + '"><span class="n">' + l[0] + "</span>" + E(l[1]) + "</div>";
      }).join("") + "</div>";
    } else if (!ref.line) h += '<p class="pill err">not found on disk</p>';
    return h;
  }
  function editBtn(ln) {
    return ln && ln.path ? '<p><button class="btn tiny js-edit" data-path="' + E(ln.path) + '">Edit ' + E(ln.path.split("/").pop()) + "</button></p>" : "";
  }
  function laneOf(key) {
    var id = key.split("@")[1] || key.split(":")[1];
    return (V.data.lanes || []).filter(function (l) { return l.id === id; })[0];
  }
  function ovOf(ln) {
    var o = V.data.overlay;
    return o && o.lanes && ln ? o.lanes[ln.id] : null;
  }
  function errHtml(e) { return e ? '<p class="pill err">' + E(e) + "</p>" : ""; }

  V.detail = function (key) {
    var n = V.nodes[key] || {}, d = V.data, h = [];
    if (key.indexOf("lane:") === 0) {
      var ln = laneOf(key);
      h.push("<h3>" + E(ln.id) + "</h3><p class=\"muted\">" + E([ln.building, ln.kind].join(" · ")) + "</p>");
      (ln.errors || []).forEach(function (e) { h.push(errHtml(e)); });
      h.push("<dl class=\"kv\"><dt>lines</dt><dd>" + E((ln.lines || []).join(", ")) + "</dd><dt>reads</dt><dd>" + E((ln.domains || []).join(", ")) +
        "</dd><dt>handler</dt><dd>" + E(ln.handler || "none") + "</dd><dt>context</dt><dd>" + (ln.context ? ln.context.length + " steps" : "no list yet") + "</dd></dl>");
      h.push(editBtn(ln));
      if (ln.kind_ref) h.push("<h4>Question class</h4>" + where({ path: ln.kind_ref.path, line: ln.kind_ref.line, snippet: [] }));
      h.push(where(ln.file));
      return h.join("");
    }
    if (n.kind === "reader") {
      h.push("<h3>game facts: " + E(n.title) + '</h3><p class="muted">StrategosReaders.Register("' + E(n.title) + '") — one domain of the per-minute state line.</p>');
      var users = (d.lanes || []).filter(function (l) { return (l.domains || []).indexOf(n.title) >= 0; }).map(function (l) { return l.id; });
      h.push('<p class="small">read by: ' + E(users.join(", ") || "no lane") + "</p>");
      h.push(where(n.ref));
    } else if (n.kind === "step") {
      var st = n.data;
      h.push("<h3>context step " + st.n + ": " + E(st.label) + "</h3>");
      h.push(errHtml(st.error));
      h.push('<dl class="kv"><dt>params</dt><dd class="mono">' + E((st.params || []).join(", ") || "none") + '</dd><dt>factory</dt><dd class="mono">' +
        E(st.label + (st.signature || "")) + "</dd><dt>spec</dt><dd class=\"mono\">" + E(JSON.stringify(st.spec)) + "</dd></dl>");
      if (st.doc) h.push('<p class="small">' + E(st.doc) + "</p>");
      if (n.lane === null) {
        var sharers = (d.lanes || []).filter(function (l) { return l.context && l.context.length; }).map(function (l) { return l.id; });
        h.push('<p class="small muted">shared: the first ' + d.trunk + " steps are the same in " + E(sharers.join(", ")) + ".</p>");
      } else {
        h.push(editBtn(n.ln));
        if (n.ln.context_ref) h.push("<h4>Listed in</h4>" + where(n.ln.context_ref));
      }
      h.push("<h4>Step code</h4>" + where(st));
    } else if (n.kind === "filters") {
      var ln2 = n.data, o = n.ov;
      h.push("<h3>option filters · " + E(ln2.id) + "</h3>");
      h.push('<p class="small muted">A line is offered only when every rule passes and no request for it is pending. Code removes impossible options; it never picks.</p>');
      h.push(overlayLines(ln2, o));
      h.push("<h4>Rules (rules.py RULES order)</h4>" + where(d.rules_ref));
      h.push("<h4>Where the question applies them</h4>" + where(n.ref));
    } else if (n.kind === "rule") {
      var ln3 = n.ln, o3 = n.ov, r = n.data, hits = [];
      h.push("<h3>filter: " + E(r.label) + '</h3><p class="muted small">' + E(ln3.id) + "</p>");
      h.push(errHtml(r.error));
      if (r.sub) h.push('<p class="small">' + E(r.sub) + "</p>");
      if (o3 && o3.dropped) Object.keys(o3.dropped).forEach(function (line) {
        if (o3.dropped[line].rule === r.label) hits.push("<li><b>" + E(line) + "</b>: " + E(o3.dropped[line].reason) + "</li>");
      });
      if (hits.length) h.push("<p class=\"small\">dropped at m" + E(o3.minute) + ":</p><ul class=\"small\">" + hits.join("") + "</ul>");
      h.push(where(r));
    } else if (n.kind === "question") {
      var q = n.ref || {};
      h.push("<h3>wording · " + E(n.title) + "</h3>");
      h.push('<p>' + E(q.instructions || "") + '</p><dl class="kv">' + Object.keys(q.criteria || {}).map(function (k) {
        return "<dt>" + E(k) + "</dt><dd>" + E(q.criteria[k]) + "</dd>";
      }).join("") + "</dl>");
      h.push(editBtn(n.data));
      h.push(where(q));
    } else if (n.kind === "models") {
      h.push("<h3>model registry</h3><p class=\"small muted\">asker/models/&lt;name&gt;.py exporting ADAPTER; the run picks one (ask.py --model).</p>");
      h.push(where(n.ref));
    } else if (key.indexOf("model:") === 0) {
      var m = ((d.models || {}).models || []).filter(function (x) { return x.id === key; })[0] || {};
      h.push("<h3>model: " + E(m.label) + '</h3><p class="muted small">' + E(m.sub || "") + "</p>" + errHtml(m.error) + where(m));
    } else if (n.kind === "answer") {
      var ln4 = n.data, o4 = n.ov;
      h.push("<h3>answer · " + E(ln4.id) + "</h3>");
      if (!o4) h.push('<p class="muted">No question of this block in the newest run' + (d.overlay && d.overlay.run ? " (" + E(d.overlay.run) + ")" : "") + ".</p>");
      else {
        if (o4.minute !== undefined && o4.minute !== null) {
          h.push('<dl class="kv"><dt>run</dt><dd class="small">' + E(d.overlay.run) + "</dd><dt>minute</dt><dd>" + E(o4.minute) + " · q#" + E(o4.qid) +
            "</dd><dt>model</dt><dd>" + E(o4.model) + "</dd><dt>offered</dt><dd>" + E((o4.offered || []).join(", ")) +
            "</dd><dt>answer</dt><dd><b>" + E(o4.choice || "-") + "</b></dd><dt>outcome</dt><dd>" + E(o4.outcome || "-") + (o4.error ? " · " + E(o4.error) : "") +
            "</dd><dt>request</dt><dd class=\"mono\">" + E(o4.request ? JSON.stringify(o4.request) : "-") + "</dd></dl>");
          h.push("<h4>Acknowledgements (engine.log)</h4>" + ((o4.acks || []).length ? "<ul class=\"small mono\">" + o4.acks.map(function (a) {
            return "<li>" + E(a.text) + "</li>";
          }).join("") + "</ul>" : '<p class="small muted">none yet</p>'));
        }
        h.push(overlayLines(ln4, o4));
      }
      h.push("<h4>Code</h4>" + where(n.ref));
    } else if (n.kind === "actor") {
      var a = n.data;
      h.push("<h3>" + E(a.label) + '</h3><p class="muted small">' + E(a.sub || "") + "</p>" + errHtml(a.error));
      if (a.acks) h.push('<ul class="small">' + a.acks.map(function (k) { return "<li>" + E(k.state) + " · line " + k.line + "</li>"; }).join("") +
        '</ul><p class="small muted">Each ack line goes back to the asker (actor/pending.py): settled (finished / dropped / rejected) frees the line, so it can be offered again.</p>');
      if (a.id === "act:petra") h.push("<h4>Handlers by block kind</h4>" + where(d.handlers_ref));
      h.push(where(a));
    }
    return h.join("");
  };

  function overlayLines(ln, o) {
    if (!o) return '<p class="small muted">No question of this block in the newest run.</p>';
    var h = [];
    if (o.offered) {
      h.push('<p class="small">m' + E(o.minute) + ", lines:</p><ul class=\"small\">" + (ln.lines || []).map(function (line) {
        if (o.offered.indexOf(line) >= 0) return '<li class="okt">✓ <b>' + E(line) + "</b> offered</li>";
        var x = (o.dropped || {})[line] || {};
        return '<li class="drop">✗ <b>' + E(line) + "</b> " + E(x.rule || "") + ": " + E(x.reason || "") + "</li>";
      }).join("") + "</ul>");
    }
    return h.join("");
  }
})();
