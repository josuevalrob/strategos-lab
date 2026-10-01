/* Economy over time: every seat's [strategos] economy lines (StrategosEconomy.js, one per
   player per game minute), e.g. Jev (p1, blue) vs Petra (p2, red), on the selected run. */
"use strict";

(function () {
  var V = Lab.views.economy = { run: null, data: null, timer: null };
  var COLORS = { blue: "#2f6fdb", red: "#d64545", green: "#3f9d3f", yellow: "#c99a00", teal: "#1a9c9c",
    purple: "#8e5bd6", orange: "#e07b1a", grey: "#7a7a7a", white: "#9aa4b2" };
  var RES = ["food", "wood", "stone", "metal"];
  var sum = function (o) { return RES.reduce(function (a, r) { return a + ((o || {})[r] || 0); }, 0); };
  var METRICS = [
    { t: "Gathered food", f: function (r) { return (r.gathered || {}).food || 0; } },
    { t: "Gathered wood", f: function (r) { return (r.gathered || {}).wood || 0; } },
    { t: "Gathered stone", f: function (r) { return (r.gathered || {}).stone || 0; } },
    { t: "Gathered metal", f: function (r) { return (r.gathered || {}).metal || 0; } },
    { t: "Stock (all four)", f: function (r) { return sum(r.stock); } },
    { t: "Workers food", f: function (r) { return (r.workers || {}).food || 0; } },
    { t: "Workers wood", f: function (r) { return (r.workers || {}).wood || 0; } },
    { t: "Workers stone + metal", f: function (r) { return ((r.workers || {}).stone || 0) + ((r.workers || {}).metal || 0); } },
    { t: "Population", f: function (r) { return r.pop || 0; } },
    { t: "Civilians", f: function (r) { return r.civilians || 0; } },
    { t: "Soldiers", f: function (r) { return r.soldiers || 0; } },
    { t: "Upgrades done", f: function (r) { return (r.upgrades || []).length; } }
  ];

  V.init = function () {
    document.getElementById("eco-run").addEventListener("change", function (e) { V.open(e.target.value); });
  };

  V.shown = function () {
    Lab.get("/api/runs").then(function (res) {
      var runs = res.runs.slice().sort(function (a, b) { return b.mtime - a.mtime; });
      var want = V.run || (Lab.views.game && Lab.views.game.run) || Lab.store.get("lab.game.run", null);
      if (!runs.some(function (r) { return r.id === want; })) want = runs.length ? runs[0].id : null;
      document.getElementById("eco-run").innerHTML = runs.map(function (r) {
        var who = (r.seats || []).map(function (s) { return s.label; }).join(" vs ");
        return '<option value="' + Lab.esc(r.id) + '"' + (r.id === want ? " selected" : "") + ">" +
          Lab.esc(r.id.replace(/^(results|runs)\//, "") + "  ·  " + (who || "?") + (r.live ? "  ·  LIVE" : "")) + "</option>";
      }).join("");
      if (want) V.open(want);
    }).catch(function (e) { Lab.toast("Runs: " + e.message, true); });
    if (!V.timer) V.timer = setInterval(function () {
      if (Lab.state.tab === "economy" && V.data && V.data.run && V.data.run.live) V.open(V.run);
    }, 5000);
  };

  V.open = function (rid) {
    V.run = rid;
    Lab.get("/api/economy", { run: rid }).then(function (res) {
      if (rid !== V.run) return;
      V.data = res;
      V.render();
    }).catch(function (e) { Lab.toast("Economy: " + e.message, true); });
  };

  function chart(m, seats, byPlayer, maxMin) {
    var W = 280, H = 130, L = 40, R = 8, T = 8, B = 20;
    var vals = [];
    seats.forEach(function (s) { (byPlayer[s.id] || []).forEach(function (r) { vals.push(m.f(r)); }); });
    var top = Math.max(1, Math.max.apply(null, vals.concat([0])));
    var x = function (min) { return L + (W - L - R) * (maxMin ? min / maxMin : 0); };
    var y = function (v) { return H - B - (H - B - T) * (v / top); };
    var lines = seats.map(function (s) {
      var rows = byPlayer[s.id] || [];
      var c = COLORS[s.color] || "var(--text)";
      var pts = rows.map(function (r) { return x(r.m).toFixed(1) + "," + y(m.f(r)).toFixed(1); }).join(" ");
      var dots = rows.map(function (r) {
        return '<circle cx="' + x(r.m).toFixed(1) + '" cy="' + y(m.f(r)).toFixed(1) + '" r="2" fill="' + c + '"><title>' +
          Lab.esc(s.label + " m" + r.m + ": " + m.f(r)) + "</title></circle>";
      }).join("");
      return '<polyline fill="none" stroke="' + c + '" stroke-width="2" points="' + pts + '"/>' + dots;
    }).join("");
    var axis = '<line x1="' + L + '" y1="' + (H - B) + '" x2="' + (W - R) + '" y2="' + (H - B) + '" stroke="var(--border)"/>' +
      '<text x="' + (L - 4) + '" y="' + (T + 8) + '" text-anchor="end" class="eco-ax">' + top + "</text>" +
      '<text x="' + (L - 4) + '" y="' + (H - B) + '" text-anchor="end" class="eco-ax">0</text>' +
      '<text x="' + L + '" y="' + (H - 4) + '" class="eco-ax">m0</text>' +
      '<text x="' + (W - R) + '" y="' + (H - 4) + '" text-anchor="end" class="eco-ax">m' + maxMin + "</text>";
    return '<div class="eco-card"><div class="eco-title">' + Lab.esc(m.t) + '</div><svg viewBox="0 0 ' + W + " " + H +
      '" width="100%" role="img" aria-label="' + Lab.esc(m.t) + '">' + axis + lines + "</svg></div>";
  }

  V.render = function () {
    var d = V.data, box = document.getElementById("eco-body");
    var seats = d.seats || [];
    var st = document.getElementById("eco-status");
    st.textContent = (d.run && d.run.live ? "live · " : "") + (d.rows || []).length + " economy line(s)";
    if (!(d.rows || []).length) {
      box.innerHTML = '<p class="muted" style="padding:12px">No <code>[strategos] economy</code> lines in this run\'s ' +
        "engine.log: it ran before StrategosEconomy.js (or without the strategos mod).</p>";
      return;
    }
    var byPlayer = {}, maxMin = 0;
    d.rows.forEach(function (r) { (byPlayer[r.player] = byPlayer[r.player] || []).push(r); maxMin = Math.max(maxMin, r.m); });
    var legend = '<div class="eco-legend">' + seats.map(function (s) {
      return '<span><i style="background:' + (COLORS[s.color] || "var(--text)") + '"></i>' + Lab.esc(s.label) + "</span>";
    }).join("") + "</div>";
    var charts = '<div class="eco-grid">' + METRICS.map(function (m) { return chart(m, seats, byPlayer, maxMin); }).join("") + "</div>";
    // Phase and new upgrades per minute, one column per seat.
    var mins = [];
    for (var i = 0; i <= maxMin; i++) mins.push(i);
    var at = {};
    d.rows.forEach(function (r) { at[r.player + ":" + r.m] = r; });
    var table = '<table class="eco-table"><thead><tr><th>min</th>' + seats.map(function (s) {
      return "<th>" + Lab.esc(s.label) + "</th>";
    }).join("") + "</tr></thead><tbody>" + mins.map(function (mm) {
      return "<tr><td>" + mm + "</td>" + seats.map(function (s) {
        var r = at[s.id + ":" + mm], prev = at[s.id + ":" + (mm - 1)];
        if (!r) return "<td></td>";
        var had = (prev && prev.upgrades) || [];
        var fresh = (r.upgrades || []).filter(function (u) { return had.indexOf(u) < 0; });
        return "<td>" + Lab.esc(r.phase) + " · pop " + r.pop + " · stock " + RES.map(function (k) { return (r.stock || {})[k] || 0; }).join("/") +
          (fresh.length ? ' · <b>+' + Lab.esc(fresh.join(", ")) + "</b>" : "") + (r.state && r.state !== "active" ? " · " + Lab.esc(r.state) : "") + "</td>";
      }).join("") + "</tr>";
    }).join("") + "</tbody></table>";
    box.innerHTML = legend + charts + table;
  };
})();
