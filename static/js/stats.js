/* Game statistics panel (Game tab, top right): the per-minute "[strategos] stats" lines,
   both players side by side, laid out like 0 A.D.'s summary screen.  Polls while live. */
"use strict";

(function () {
  var S = Lab.views.stats = { run: null, rows: [], tab: "score", at: "latest", metric: "total", busy: false };
  var COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#eab308", "#a855f7", "#14b8a6", "#f97316", "#ec4899"];
  var RES = ["food", "wood", "stone", "metal"];

  function tot(o) {
    if (o === null || o === undefined) return 0;
    if (typeof o !== "object") return +o || 0;
    if (o.total !== undefined) return +o.total || 0;
    return Object.keys(o).reduce(function (a, k) { return a + (+o[k] || 0); }, 0);
  }
  function st(p) { return p.stats || {}; }
  function gathered(p) { var g = st(p).resourcesGathered || {}; return RES.reduce(function (a, r) { return a + (+g[r] || 0); }, 0); }
  function eco(p) { return Math.round((gathered(p) + (+st(p).tradeIncome || 0)) / 10); }
  function mil(p) { var s = st(p); return Math.round(((+s.enemyUnitsKilledValue || 0) + (+s.unitsCapturedValue || 0) + (+s.enemyBuildingsDestroyedValue || 0) + (+s.buildingsCapturedValue || 0)) / 10); }
  function expl(p) { return Math.round((+st(p).percentMapExplored || 0) * 10); }
  function al(p, k) { return (p.alive || {})[k] || 0; }

  // [label, value(p), higher is better?]
  var TABS = {
    score: [["Total score", function (p) { return eco(p) + mil(p) + expl(p); }, 1],
      ["Economy score", eco, 1], ["Military score", mil, 1], ["Exploration score", expl, 1],
      ["Population", function (p) { return p.pop + " / " + p.popLimit; }, 0, function (p) { return p.pop; }],
      ["Phase", function (p) { return p.phase || "-"; }, 0, null]],
    units: [["Alive", function (p) { return al(p, "units"); }, 1], ["Soldiers", function (p) { return al(p, "soldiers"); }, 1],
      ["Workers", function (p) { return al(p, "workers"); }, 1], ["Cavalry", function (p) { return al(p, "cavalry"); }, 1],
      ["Champions", function (p) { return al(p, "champions"); }, 1], ["Heroes", function (p) { return al(p, "heroes"); }, 1],
      ["Siege", function (p) { return al(p, "siege"); }, 1], ["Trained", function (p) { return tot(st(p).unitsTrained); }, 1],
      ["Lost", function (p) { return tot(st(p).unitsLost); }, -1], ["Enemies killed", function (p) { return tot(st(p).enemyUnitsKilled); }, 1]],
    buildings: [["Standing", function (p) { return al(p, "structures"); }, 1],
      ["Constructed", function (p) { return tot(st(p).buildingsConstructed); }, 1],
      ["Lost", function (p) { return tot(st(p).buildingsLost); }, -1],
      ["Enemy destroyed", function (p) { return tot(st(p).enemyBuildingsDestroyed); }, 1],
      ["Map control %", function (p) { return +st(p).percentMapControlled || 0; }, 1]],
    resources: RES.map(function (r) { return ["Stock " + r, function (p) { return Math.floor((p.stock || {})[r] || 0); }, 1]; })
      .concat(RES.map(function (r) { return ["Gathered " + r, function (p) { return Math.round((st(p).resourcesGathered || {})[r] || 0); }, 1]; }))
      .concat([["Gathered total", function (p) { return Math.round(gathered(p)); }, 1],
        ["Used total", function (p) { return Math.round(tot(st(p).resourcesUsed)); }, 0]]),
    military: [["Kills", function (p) { return tot(st(p).enemyUnitsKilled); }, 1],
      ["Kills value", function (p) { return Math.round(+st(p).enemyUnitsKilledValue || 0); }, 1],
      ["Losses", function (p) { return tot(st(p).unitsLost); }, -1],
      ["Losses value", function (p) { return Math.round(+st(p).unitsLostValue || 0); }, -1],
      ["Kill / loss", function (p) { var l = tot(st(p).unitsLost); return l ? Math.round(tot(st(p).enemyUnitsKilled) / l * 100) / 100 : "-"; }, 1],
      ["Buildings destroyed", function (p) { return tot(st(p).enemyBuildingsDestroyed); }, 1],
      ["Buildings lost", function (p) { return tot(st(p).buildingsLost); }, -1]]
  };
  var CHART = { total: TABS.score[0], economy: TABS.score[1], military: TABS.score[2], pop: ["Population", function (p) { return p.pop; }],
    soldiers: TABS.units[1], gathered: TABS.resources[8], kills: TABS.military[0], losses: TABS.military[2], control: TABS.buildings[4] };

  function row(minute) {
    if (!S.rows.length) return null;
    if (minute === null || minute === undefined) return S.rows[S.rows.length - 1];
    var best = S.rows[0];
    S.rows.forEach(function (r) { if (r.m <= minute) best = r; });
    return best;
  }

  function table(r) {
    var ps = r.players || [];
    var h = '<table class="st-table"><thead><tr><th></th>' + ps.map(function (p, i) {
      return '<th><span class="st-dot" style="background:' + COLORS[i % COLORS.length] + '"></span>P' + p.id + " " + Lab.esc(p.name || "") +
        ' <span class="muted small">' + Lab.esc(p.civ || "") + "</span>" + (p.state && p.state !== "active" ? ' <span class="pill ' + (p.state === "won" ? "ok" : "err") + '">' + Lab.esc(p.state) + "</span>" : "") + "</th>";
    }).join("") + "</tr></thead><tbody>";
    TABS[S.tab].forEach(function (m) {
      var vals = ps.map(function (p) { return m[1](p); });
      var num = ps.map(function (p, i) { return m[3] === null ? null : m[3] ? m[3](p) : vals[i]; });
      var lead = -1;
      if (m[2] && ps.length > 1 && num.every(function (v) { return typeof v === "number"; })) {
        var best = m[2] > 0 ? Math.max.apply(null, num) : Math.min.apply(null, num);
        if (num.filter(function (v) { return v === best; }).length === 1) lead = num.indexOf(best);
      }
      h += "<tr><td>" + Lab.esc(m[0]) + "</td>" + vals.map(function (v, i) {
        return '<td class="' + (i === lead ? "st-lead" : "") + '">' + Lab.esc(v) + "</td>";
      }).join("") + "</tr>";
    });
    return h + "</tbody></table>";
  }

  function chart() {
    var m = CHART[S.metric], rows = S.rows;
    if (!rows.length) return "";
    var n = Math.max.apply(null, rows.map(function (r) { return (r.players || []).length; }));
    var series = [];
    for (var i = 0; i < n; i++) series.push(rows.map(function (r) { var p = (r.players || [])[i]; return p ? +m[1](p) || 0 : 0; }));
    var W = 520, H = 170, pad = 30, maxY = Math.max(1, Math.max.apply(null, series.map(function (s) { return Math.max.apply(null, s); })));
    var maxX = Math.max(1, rows[rows.length - 1].m || 1), minX = rows[0].m || 0;
    function x(mm) { return pad + (mm - minX) / Math.max(1, maxX - minX) * (W - pad - 8); }
    function y(v) { return H - 18 - v / maxY * (H - 30); }
    var svg = '<svg class="st-chart" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none">' +
      '<line x1="' + pad + '" y1="' + (H - 18) + '" x2="' + W + '" y2="' + (H - 18) + '" class="st-axis"/>' +
      '<text x="2" y="14" class="st-lbl">' + Math.round(maxY) + '</text><text x="' + pad + '" y="' + (H - 4) + '" class="st-lbl">min ' + minX + '</text>' +
      '<text x="' + (W - 50) + '" y="' + (H - 4) + '" class="st-lbl">min ' + maxX + "</text>";
    series.forEach(function (s, i) {
      svg += '<polyline fill="none" stroke-width="2" stroke="' + COLORS[i % COLORS.length] + '" points="' +
        s.map(function (v, k) { return x(rows[k].m) + "," + y(v); }).join(" ") + '"/>';
    });
    return '<p><select class="js-metric">' + Object.keys(CHART).map(function (k) {
      return '<option value="' + k + '"' + (k === S.metric ? " selected" : "") + ">" + Lab.esc(CHART[k][0]) + "</option>";
    }).join("") + "</select></p>" + svg + "</svg>";
  }

  S.render = function () {
    var el = document.getElementById("game-stats"), V = Lab.views.game;
    if (!el) return;
    if (!S.rows.length) {
      el.innerHTML = '<p class="muted small">No stats lines in this run\'s engine.log. Games started with the strategos mod from commit 1226e3a9a1 on write one per game minute.</p>';
      return;
    }
    var qmin = V && V.lastIo && V.lastIo.entry ? V.lastIo.entry.minute : null;
    var atQ = S.at === "question" && qmin !== null && qmin !== undefined;
    var r = row(atQ ? Math.floor(qmin) : null);
    var tabs = ["score", "units", "buildings", "resources", "military", "charts"];
    el.innerHTML = '<div class="st-head"><b>Minute ' + Lab.esc(r.m) + "</b>" +
      '<span class="seg st-at"><button data-at="latest" class="' + (atQ ? "" : "on") + '">Latest</button>' +
      '<button data-at="question" class="' + (atQ ? "on" : "") + '"' + (qmin === null || qmin === undefined ? " disabled" : "") + ">At selected question" +
      (qmin !== null && qmin !== undefined ? " (min " + Math.floor(qmin) + ")" : "") + "</button></span></div>" +
      '<div class="io-tabs st-tabs">' + tabs.map(function (t) {
        return '<button data-t="' + t + '"' + (t === S.tab ? ' class="on"' : "") + ">" + t.charAt(0).toUpperCase() + t.slice(1) + "</button>";
      }).join("") + "</div>" + (S.tab === "charts" ? chart() : table(r));
    el.querySelectorAll(".st-tabs button").forEach(function (b) { b.addEventListener("click", function () { S.tab = b.dataset.t; S.render(); }); });
    el.querySelectorAll(".st-at button").forEach(function (b) { b.addEventListener("click", function () { S.at = b.dataset.at; S.render(); }); });
    var sel = el.querySelector(".js-metric");
    if (sel) sel.addEventListener("change", function () { S.metric = sel.value; S.render(); });
  };

  S.load = function () {
    var V = Lab.views.game;
    if (!V || !V.run || S.busy) return;
    S.busy = true;
    var run = V.run;
    Lab.get("/api/stats", { run: run }).then(function (res) {
      var changed = run !== S.run || res.minutes.length !== S.rows.length;
      S.run = run; S.rows = res.minutes;
      if (changed) S.render();
    }).catch(function () {}).then(function () { S.busy = false; });
  };

  var lastSel = null;
  setInterval(function () {
    var V = Lab.views.game;
    if (!V) return;
    if (V.run !== S.run || V.live) S.load();
    var sel = V.lastIo && V.lastIo.entry ? V.run + "#" + V.lastIo.entry.qid : null;
    if (sel !== lastSel) { lastSel = sel; if (S.at === "question") S.render(); else if (S.rows.length) S.render(); }
  }, 2000);
})();
