/* L1: the Map view -- one civ.  3D (default): what the model READS on one plane, what it CAN
   PICK on the other, meeting at play.  2D: the flat Cytoscape map. */
"use strict";

(function () {
  var V = Lab.views.map = { civ: null, graph: null, g3: null, loaded: null, loaded3: null,
    pendingSelect: null, dim: "3d", data: null };

  V.init = function () {
    V.graph = new Lab.Graph(document.getElementById("map-cy"), { onSelect: V.detail });
    V.g3 = new Lab.Graph3D(document.getElementById("map-3d"), {
      onSelect: V.detail,
      onClear: function () { V.intro(); }
    });
    document.getElementById("map-civ").addEventListener("change", function (e) {
      V.load(e.target.value);
    });
    document.getElementById("map-fit").addEventListener("click", function () {
      if (V.dim === "3d") V.g3.fit(); else V.graph.fit();
    });
    document.getElementById("map-clone").addEventListener("click", function () {
      Lab.cloneFlow(V.civ || "spart");
    });
    document.querySelectorAll("#map-dim button").forEach(function (b) {
      b.addEventListener("click", function () { V.setDim(b.dataset.d); });
    });
    V.civ = Lab.store.get("lab.map.civ", "spart");
    V.dim = Lab.store.get("lab.map.dim", "3d");
  };

  V.setDim = function (dim) {
    V.dim = dim;
    Lab.store.set("lab.map.dim", dim);
    document.querySelectorAll("#map-dim button").forEach(function (b) { b.classList.toggle("on", b.dataset.d === dim); });
    document.getElementById("map-cy").classList.toggle("hidden", dim !== "2d");
    document.getElementById("map-3d").classList.toggle("hidden", dim !== "3d");
    document.getElementById("map-legend3d").classList.toggle("hidden", dim !== "3d");
    document.querySelector("#view-map .legend2d").classList.toggle("hidden", dim !== "2d");
    if (dim === "3d") V.ensure3d(); else if (V.loaded === V.civ) { V.graph.fit(); V.selectPending(); }
  };

  V.shown = function (arg) {
    if (arg && arg.civ) V.civ = arg.civ;
    if (arg && arg.node) V.pendingSelect = arg.node;
    V.setDim(V.dim);
    if (V.loaded !== V.civ) V.load(V.civ);
    else if (V.dim === "2d") {
      V.graph.fit();
      V.selectPending();
    }
  };

  V.selectPending = function () {
    if (!V.pendingSelect) return;
    var g = V.dim === "3d" ? V.g3 : V.graph;
    if (!g.byId) return;
    var n = g.byId[V.pendingSelect];
    if (n) { g.select(n.id); V.detail(n); }
    V.pendingSelect = null;
  };

  V.ensure3d = function () {
    if (V.loaded3 === V.civ) { V.g3.resize(); V.selectPending(); return Promise.resolve(); }
    var civ = V.civ;
    return Lab.get("/api/map3d", { civ: civ }).then(function (data) {
      V.legend3d(data.legend || []);
      return V.g3.load(data).then(function () {
        V.loaded3 = civ;
        V.selectPending();
      });
    }).catch(function (e) {
      // No WebGL, or an older lab server without /api/map3d: fall back to 2D and say why.
      var old = /not found/i.test(e.message);
      Lab.toast("3D map unavailable: " + (old ? "this lab server predates it (restart lab.py)" : e.message) +
        ". Showing 2D.", true);
      V.setDim("2d");
    });
  };

  V.legend3d = function (legend) {
    var colors = { reads: "--c-input", picks: "--c-question", offers: "--c-part", petra: "--c-action",
      describes: "--c-describes", config: "--edge" };
    document.getElementById("map-legend3d").innerHTML = legend.map(function (l) {
      return '<span class="ln' + (l.dashed ? " dash" : "") + '" style="border-top-color:var(' + colors[l.kind] + ')"></span>' + Lab.esc(l.label);
    }).join("") + '<span class="muted" style="margin-left:12px">drag: orbit · right-drag: pan · wheel: zoom · click: focus</span>';
  };

  V.intro = function () {
    var data = V.data || {};
    var all = (data.warnings || []).concat(data.code_errors || []);
    document.getElementById("map-detail").innerHTML =
      '<h3>' + Lab.esc(V.civ) + '</h3><p class="muted">Click a node: its whole path lights up (what feeds it, what it leads to) and its details show here. Click empty space to clear.</p>' +
      "<ul class=\"small\"><li><b>What the model READS</b> (upper plane): the civ file, every hero file and the game state make the prompt, which goes to <b>play</b>.</li>" +
      "<li><b>What the model CAN PICK</b> (lower plane): triggers fire parts (why asked); the parts' options merge into the one play question; each option leads to a Petra action and the managers or queues that carry it out.</li>" +
      "<li>The two flows meet at <b>play</b>, between the planes. Code removes only the impossible options.</li></ul>" +
      (all.length ? '<p class="muted">Warnings:</p><ul class="small">' + all.map(function (w) { return "<li>" + Lab.esc(w) + "</li>"; }).join("") + "</ul>" : "");
  };

  V.load = function (civ) {
    return Lab.get("/api/map", { civ: civ }).then(function (data) {
      V.civ = civ;
      V.loaded = civ;
      V.data = data;
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
      V.intro();
      V.loaded3 = null;
      if (V.dim === "3d") V.ensure3d(); else V.selectPending();
      Lab.views.game && Lab.views.game.mapStale && Lab.views.game.mapStale();
    }).catch(function (e) { Lab.toast("Map: " + e.message, true); });
  };

  V.detail = function (node) {
    Lab.renderDetail(document.getElementById("map-detail"), node);
  };

  V.onTheme = function () {
    V.graph.restyle();
    V.g3.restyle();
  };
})();
