/* L1: the Map view -- one civ, inputs -> parts -> play options -> Petra action -> managers. */
"use strict";

(function () {
  var V = Lab.views.map = { civ: null, graph: null, loaded: null, pendingSelect: null };

  V.init = function () {
    V.graph = new Lab.Graph(document.getElementById("map-cy"), { onSelect: V.detail });
    document.getElementById("map-civ").addEventListener("change", function (e) {
      V.load(e.target.value);
    });
    document.getElementById("map-fit").addEventListener("click", function () { V.graph.fit(); });
    document.getElementById("map-clone").addEventListener("click", function () {
      Lab.cloneFlow(V.civ || "spart");
    });
    V.civ = Lab.store.get("lab.map.civ", "spart");
  };

  V.shown = function (arg) {
    if (arg && arg.civ) V.civ = arg.civ;
    if (arg && arg.node) V.pendingSelect = arg.node;
    if (V.loaded !== V.civ) V.load(V.civ);
    else {
      V.graph.fit();
      V.selectPending();
    }
  };

  V.selectPending = function () {
    if (!V.pendingSelect || !V.graph.byId) return;
    var n = V.graph.byId[V.pendingSelect];
    if (n) { V.graph.select(n.id); V.detail(n); }
    V.pendingSelect = null;
  };

  V.load = function (civ) {
    return Lab.get("/api/map", { civ: civ }).then(function (data) {
      V.civ = civ;
      V.loaded = civ;
      Lab.store.set("lab.map.civ", civ);
      var sel = document.getElementById("map-civ");
      sel.innerHTML = data.civs.map(function (c) {
        return '<option value="' + Lab.esc(c) + '"' + (c === civ ? " selected" : "") + ">" + Lab.esc(c) + "</option>";
      }).join("");
      V.graph.load(data);
      var warn = document.getElementById("map-warn");
      var all = (data.warnings || []).concat(data.code_errors || []);
      warn.textContent = all.length ? all.length + " warning(s)" : "";
      warn.title = all.join("\n");
      document.getElementById("map-detail").innerHTML =
        '<h3>' + Lab.esc(civ) + '</h3><p class="muted">Click a node to see where it comes from and what the model reads.</p>' +
        "<ul class=\"small\"><li><b>Inputs</b>: the civ file, every hero file and the game state are the prompt.</li>" +
        "<li><b>Why asked</b>: each part adds its options when its trigger fires; they merge into one play question per turn.</li>" +
        "<li><b>play options</b>: what the model can pick (code removes only the impossible ones).</li>" +
        "<li><b>Petra action</b>: what the head sends, and the Petra managers or queues that carry it out.</li></ul>" +
        (all.length ? '<p class="muted">Warnings:</p><ul class="small">' + all.map(function (w) { return "<li>" + Lab.esc(w) + "</li>"; }).join("") + "</ul>" : "");
      V.selectPending();
      Lab.views.game && Lab.views.game.mapStale && Lab.views.game.mapStale();
    }).catch(function (e) { Lab.toast("Map: " + e.message, true); });
  };

  V.detail = function (node) {
    Lab.renderDetail(document.getElementById("map-detail"), node);
  };

  V.onTheme = function () { V.graph.restyle(); };
})();
