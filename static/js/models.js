/* L7: Models over time -- per model x Q&A version, the options each question kind got. */
"use strict";

(function () {
  var V = Lab.views.models = { data: null, kind: null };
  var PALETTE = ["#2563eb", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#65a30d",
    "#dc2626", "#475569", "#9333ea", "#0d9488", "#ca8a04"];

  function colorOf(opt) {
    if (opt.indexOf("(no answer") === 0) return "#94a3b8";
    var h = 0;
    for (var i = 0; i < opt.length; i++) h = (h * 31 + opt.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }

  V.init = function () {
    V.kind = Lab.store.get("lab.models.kind", "play");
    document.getElementById("models-kind").addEventListener("change", function (e) {
      V.kind = e.target.value;
      Lab.store.set("lab.models.kind", V.kind);
      V.render();
    });
  };

  V.shown = function () {
    Lab.get("/api/models").then(function (d) {
      V.data = d;
      if (d.kinds.indexOf(V.kind) < 0) V.kind = d.kinds.indexOf("play") >= 0 ? "play" : d.kinds[0];
      document.getElementById("models-kind").innerHTML = d.kinds.map(function (k) {
        var n = d.groups.filter(function (g) { return g.kinds[k]; }).length;
        return '<option value="' + Lab.esc(k) + '"' + (k === V.kind ? " selected" : "") + ">" + Lab.esc(k) +
          " (" + n + " model/version group" + (n === 1 ? "" : "s") + ")</option>";
      }).join("");
      V.render();
    }).catch(function (e) { Lab.toast("Models: " + e.message, true); });
  };

  V.render = function () {
    var d = V.data, kind = V.kind, body = document.getElementById("models-body");
    if (!d) return;
    var byModel = {}, hidden = [];
    d.groups.forEach(function (g) {
      if (!g.kinds[kind]) { hidden.push(g.model + " @ " + g.qa_version); return; }
      (byModel[g.model] = byModel[g.model] || []).push(g);
    });
    var html = [];
    Object.keys(byModel).sort().forEach(function (model) {
      byModel[model].forEach(function (g) {
        var k = g.kinds[kind];
        var when = g.last ? new Date(g.last * 1000).toLocaleDateString() : "";
        html.push('<div class="card"><h4>' + Lab.esc(model) + "</h4>" +
          '<div class="sub">Q&amp;A ' + Lab.esc(g.qa_version) + " · " + g.runs.length + " run" + (g.runs.length === 1 ? "" : "s") +
          (when ? " · " + Lab.esc(when) : "") + '<br><span class="small">' + g.runs.map(Lab.esc).join(", ") + "</span></div>" +
          V.dist(k) + "</div>");
      });
    });
    if (hidden.length) {
      html.push('<p class="muted small" style="grid-column:1/-1;margin:0">Not shown (no ' + Lab.esc(kind) + " questions): " +
        hidden.map(Lab.esc).join(", ") + "</p>");
    }
    // All kinds x groups: how many questions each got.
    var kinds = d.kinds;
    html.push('<div class="card models-table"><h4>Questions per kind</h4><div class="sub">Rows in the advisor logs (every run found under .claude/strategos/results and runs).</div>' +
      '<table class="grid"><tr><th>model</th><th>Q&amp;A</th><th>runs</th>' + kinds.map(function (k) { return "<th>" + Lab.esc(k) + "</th>"; }).join("") + "</tr>" +
      d.groups.map(function (g) {
        return "<tr><td>" + Lab.esc(g.model) + "</td><td><code>" + Lab.esc(g.qa_version) + "</code></td><td class=\"n\">" + g.runs.length + "</td>" +
          kinds.map(function (k) { return '<td class="n">' + (g.kinds[k] ? g.kinds[k].rows : "") + "</td>"; }).join("") + "</tr>";
      }).join("") + "</table></div>");
    body.innerHTML = html.join("");
  };

  V.dist = function (k) {
    var total = k.rows;
    var opts = Object.keys(k.choices).sort(function (a, b) { return k.choices[b] - k.choices[a]; });
    return '<div class="small muted" style="margin-bottom:6px">' + total + " question(s)</div><div class=\"dist\">" +
      opts.map(function (o) {
        var n = k.choices[o], pct = Math.round(100 * n / total);
        return '<span class="name" title="' + Lab.esc(o) + '">' + Lab.esc(o) + "</span>" +
          '<div class="track"><div class="fill" style="width:' + Math.max(1, pct) + "%;background:" + colorOf(o) + '"></div></div>' +
          '<span class="num">' + n + " · " + pct + "%</span>";
      }).join("") + "</div>";
  };
})();
