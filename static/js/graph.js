/* The map graph (Cytoscape), shared by the Map and Game views. */
"use strict";

(function () {
  var COL_X = [0, 290, 580, 880, 1170];
  var NODE_W = 210;

  function lineCount(label) {
    return String(label).split("\n").reduce(function (n, part) {
      return n + Math.max(1, Math.ceil(part.length / 30));
    }, 0);
  }

  function styleSheet() {
    var c = Lab.css;
    return [
      { selector: "node", style: {
        "shape": "round-rectangle", "width": NODE_W, "height": "data(h)",
        "background-color": c("--node-fill"), "border-width": 2, "border-color": c("--c-manager"),
        "label": "data(label)", "color": c("--text"), "font-size": 12, "text-wrap": "wrap",
        "text-max-width": NODE_W - 16, "text-valign": "center", "text-halign": "center",
        "font-family": "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif",
        "transition-property": "opacity, border-color, background-color", "transition-duration": "150ms"
      } },
      { selector: "node[group='input']", style: { "border-color": c("--c-input") } },
      { selector: "node[group='part']", style: { "border-color": c("--c-part") } },
      { selector: "node[group='option']", style: { "border-color": c("--c-option"),
        "font-family": "ui-monospace, SFMono-Regular, Menlo, monospace", "font-size": 11.5 } },
      { selector: "node[group='action']", style: { "border-color": c("--c-action") } },
      { selector: "node[group='manager']", style: { "border-color": c("--c-manager"), "shape": "rectangle" } },
      { selector: "node[group='queue']", style: { "border-color": c("--c-queue"), "shape": "rectangle",
        "border-style": "double", "border-width": 3 } },
      { selector: "node[?dim]", style: { "opacity": 0.55, "border-style": "dotted" } },
      { selector: "node[?bad]", style: { "border-color": c("--c-bad"), "border-style": "dashed",
        "background-color": c("--err-soft"), "border-width": 2.5 } },
      { selector: "node:parent", style: {
        "shape": "round-rectangle", "background-color": c("--c-question"), "background-opacity": 0.07,
        "border-color": c("--c-question"), "border-width": 2, "padding": "16px",
        "text-valign": "top", "text-halign": "center", "font-weight": "bold", "font-size": 13,
        "color": c("--c-question"), "text-margin-y": -6
      } },
      { selector: "node[group='colhead']", style: {
        "background-opacity": 0, "border-width": 0, "color": c("--muted"), "font-size": 12.5,
        "font-weight": "bold", "events": "no", "text-transform": "uppercase"
      } },
      { selector: "edge", style: {
        "width": 1.3, "line-color": c("--edge"), "target-arrow-color": c("--edge"),
        "target-arrow-shape": "triangle", "arrow-scale": 0.8, "curve-style": "bezier", "opacity": 0.8
      } },
      { selector: "edge[style='dashed']", style: { "line-style": "dashed", "opacity": 0.55 } },
      { selector: "edge.lab, edge:selected", style: {
        "label": "data(label)", "font-size": 10, "color": c("--muted"),
        "text-background-color": c("--bg"), "text-background-opacity": 1, "text-background-padding": 2,
        "text-rotation": "autorotate"
      } },
      { selector: "node:selected", style: { "overlay-color": c("--accent"), "overlay-opacity": 0.14,
        "overlay-padding": 4 } },
      { selector: ".faded", style: { "opacity": 0.14 } },
      { selector: "node.hl-part", style: { "opacity": 1, "border-color": c("--hl"), "border-width": 3.5 } },
      { selector: "node.hl-offered", style: { "opacity": 1 } },
      { selector: "node.hl-rule", style: { "opacity": 1, "border-style": "dashed", "border-color": c("--warn"),
        "border-width": 3 } },
      { selector: "node.hl-chosen", style: { "opacity": 1, "background-color": c("--accent"), "color": "#fff",
        "border-color": c("--accent"), "border-width": 3 } },
      { selector: "node.hl-path", style: { "opacity": 1, "border-color": c("--accent"), "border-width": 3.5 } },
      { selector: "node.hl-parent", style: { "opacity": 1 } },
      { selector: "edge.hl", style: { "opacity": 1, "line-color": c("--accent"), "target-arrow-color": c("--accent"),
        "width": 3 } },
      { selector: "edge.hl-soft", style: { "opacity": 0.7, "line-color": c("--hl"), "target-arrow-color": c("--hl"),
        "width": 2 } }
    ];
  }

  function positions(nodes, columns) {
    var cols = {};
    nodes.forEach(function (n) {
      if (n.id === "q:play") return;           // compound parent: sized by its options
      (cols[n.col] = cols[n.col] || []).push(n);
    });
    var heights = {}, maxH = 0;
    Object.keys(cols).forEach(function (k) {
      var h = 0;
      cols[k].forEach(function (n) { n._h = 14 + 15 * lineCount(n.label); h += n._h + 16; });
      heights[k] = h; maxH = Math.max(maxH, h);
    });
    var pos = {};
    Object.keys(cols).forEach(function (k) {
      var y = (maxH - heights[k]) / 2;
      cols[k].forEach(function (n) {
        pos[n.id] = { x: COL_X[n.col] || n.col * 290, y: y + n._h / 2 };
        y += n._h + 16;
      });
    });
    var heads = (columns || []).map(function (label, i) {
      return { data: { id: "colhead:" + i, label: label, group: "colhead", h: 24 },
               position: { x: COL_X[i], y: -60 }, selectable: false, grabbable: false };
    });
    return { pos: pos, heads: heads };
  }

  function Graph(container, opts) {
    this.container = container;
    this.opts = opts || {};
    this.cy = null;
    this.data = null;
  }

  Graph.prototype.load = function (data) {
    this.data = data;
    var layout = positions(data.nodes, data.columns);
    var els = layout.heads.slice();
    data.nodes.forEach(function (n) {
      var d = { id: n.id, label: n.label, group: n.group, h: n._h || 30, dim: !!n.dim, bad: !!n.bad };
      if (n.parent) d.parent = n.parent;
      var el = { data: d };
      if (layout.pos[n.id]) el.position = layout.pos[n.id];
      els.push(el);
    });
    data.edges.forEach(function (e) {
      els.push({ data: { id: e.id, source: e.source, target: e.target, label: e.label || "", style: e.style } });
    });
    if (this.cy) this.cy.destroy();
    var self = this;
    this.cy = cytoscape({
      container: this.container, elements: els, style: styleSheet(),
      layout: { name: "preset", fit: true, padding: 30 },
      minZoom: 0.15, maxZoom: 2.5, boxSelectionEnabled: false
    });
    this.byId = {};
    data.nodes.forEach(function (n) { self.byId[n.id] = n; });
    this.cy.on("tap", "node", function (evt) {
      var n = self.byId[evt.target.id()];
      if (n && self.opts.onSelect) self.opts.onSelect(n);
    });
    this.cy.on("mouseover", "node", function (evt) {
      evt.target.connectedEdges().addClass("lab");
    });
    this.cy.on("mouseout", "node", function (evt) {
      evt.target.connectedEdges().removeClass("lab");
    });
    this.fit();
  };

  Graph.prototype.fit = function () {
    if (!this.cy) return;
    this.cy.resize();
    this.cy.fit(undefined, 24);
  };

  Graph.prototype.restyle = function () {
    if (this.cy) this.cy.style(styleSheet());
  };

  Graph.prototype.select = function (id) {
    if (!this.cy) return;
    this.cy.$(":selected").unselect();
    var n = this.cy.getElementById(id);
    if (n && n.length) {
      n.select();
      this.cy.animate({ center: { eles: n }, duration: 250 });
    }
  };

  Graph.prototype.clearHighlight = function () {
    if (!this.cy) return;
    this.cy.elements().removeClass("faded hl-part hl-offered hl-rule hl-chosen hl-path hl-parent hl hl-soft lab");
  };

  /* path = {parts, offered, chosen, rule, action, managers} (server map_path). */
  Graph.prototype.highlight = function (path) {
    if (!this.cy) return;
    this.clearHighlight();
    if (!path) return;
    var cy = this.cy;
    cy.elements().not("[group='colhead']").addClass("faded");
    function on(id, cls) {
      if (!id) return null;
      var n = cy.getElementById(id);
      if (n && n.length) { n.removeClass("faded").addClass(cls); return n; }
      return null;
    }
    (path.parts || []).forEach(function (id) { on(id, "hl-part"); });
    (path.offered || []).forEach(function (id) { on(id, "hl-offered"); });
    on("q:play", "hl-parent");
    ["in:civ", "in:state"].forEach(function (id) { on(id, "hl-offered"); });
    cy.nodes("[group='input']").forEach(function (n) { if (n.id().indexOf("in:hero:") === 0) n.removeClass("faded"); });
    if (path.rule && path.rule !== path.chosen) on(path.rule, "hl-rule");
    on(path.chosen, "hl-chosen");
    on(path.action, "hl-path");
    (path.managers || []).forEach(function (id) { on(id, "hl-path"); });
    // Edges: part -> offered option (soft), chosen -> action -> managers (strong).
    cy.edges().forEach(function (e) {
      var s = e.source().id(), t = e.target().id();
      if ((path.parts || []).indexOf(s) >= 0 && (path.offered || []).indexOf(t) >= 0) e.removeClass("faded").addClass("hl-soft");
      if (s === path.chosen && t === path.action) e.removeClass("faded").addClass("hl lab");
      if (s === path.action && (path.managers || []).indexOf(t) >= 0) e.removeClass("faded").addClass("hl");
      if (t === "q:play" && !e.source().hasClass("faded")) e.removeClass("faded");
    });
    // Managers from ORDER_ROUTES when the log line named none (e.g. a repeated order).
    if (path.action && !(path.managers || []).length) {
      cy.getElementById(path.action).outgoers("edge").forEach(function (e) {
        e.removeClass("faded").addClass("hl");
        e.target().removeClass("faded").addClass("hl-path");
      });
    }
    if (this.opts.zoomToPath) {
      var lit = cy.nodes(".hl-part, .hl-offered, .hl-chosen, .hl-rule, .hl-path");
      if (lit.length) cy.animate({ fit: { eles: lit, padding: 18 }, duration: 250 });
    }
  };

  Lab.Graph = Graph;

  /* -- node details (Map view panel, also used by the Game view) ------------ */
  Lab.renderDetail = function (box, node, opts) {
    opts = opts || {};
    var d = node.details || {};
    var h = ['<h3>' + Lab.esc(d.title || node.label) + "</h3>"];
    if (node.bad) h.push('<p class="pill err">cannot work as it stands</p>');
    if (node.dim) h.push('<p class="pill warn">not asked for this civ</p>');
    if (d.instructions) h.push('<p><span class="muted">Instructions:</span></p><div class="quote">' + Lab.esc(d.instructions) + "</div>");
    if (d.trigger) h.push('<dl class="kv"><dt>Asked when</dt><dd>' + Lab.esc(d.trigger) + "</dd>" +
      "<dt>Options</dt><dd>" + Lab.esc(d.options_text) + "</dd>" +
      "<dt>Rule</dt><dd>" + Lab.esc(d.rule) + "</dd>" +
      "<dt>Set by</dt><dd>" + Lab.esc(d.who) + "</dd>" +
      (d.note ? "<dt>Here</dt><dd>" + Lab.esc(d.note) + "</dd>" : "") + "</dl>");
    if (d.criteria_text !== undefined) {
      h.push('<p><span class="muted">What the model reads for this option (play.json criteria' +
        (d.criteria_key ? ' <code>' + Lab.esc(d.criteria_key) + "</code>" : "") + "):</span></p>" +
        '<div class="quote">' + (d.criteria_text === null ? '<span class="pill err">no wording</span>' : Lab.esc(d.criteria_text)) + "</div>");
    }
    if (d.summary && d.summary.length) {
      h.push("<ul>" + d.summary.map(function (s) { return "<li>" + Lab.esc(s) + "</li>"; }).join("") + "</ul>");
    }
    if (d.criteria && Object.keys(d.criteria).length) {
      h.push('<p class="muted">Criteria (option &rarr; what the model reads):</p><dl class="kv">' +
        Object.keys(d.criteria).map(function (k) {
          return "<dt><code>" + Lab.esc(k) + "</code></dt><dd>" + Lab.esc(d.criteria[k]) + "</dd>";
        }).join("") + "</dl>");
    }
    if (d.text_lines && d.text_lines.length) {
      h.push('<p class="muted">Text (what the model reads):</p><ol class="small">' +
        d.text_lines.map(function (s) { return "<li>" + Lab.esc(s) + "</li>"; }).join("") + "</ol>");
    }
    if (d.error) h.push('<p class="pill err">' + Lab.esc(d.error) + "</p>");
    if (d.sources && d.sources.length) {
      h.push('<p class="muted">Comes from (the order list is the option list):</p><div class="anchors">' +
        d.sources.map(function (src, i) {
          return '<button class="btn tiny js-src" data-i="' + i + '">Edit ' + Lab.esc(src.label) + "</button>";
        }).join("") + "</div>");
    }
    var edit = d.edit || d.question_file;
    if (d.edit) h.push('<p><button class="btn primary js-edit">Edit ' + Lab.esc(edit.split("/").pop()) +
      (d.sources ? " (wording)" : "") + "</button></p>");
    else if (d.question_file) h.push('<p><button class="btn js-edit">Edit ' + Lab.esc(edit.split("/").pop()) +
      '</button> <span class="small muted">its own question: used only when this part is asked on its own, not inside play (play.json words the options)</span></p>');
    if (d.anchors && d.anchors.length) {
      h.push('<p class="muted">In the code (click for the source):</p><div class="anchors">' +
        d.anchors.map(function (a, i) {
          return '<button class="anchor-chip' + (a.found ? "" : " missing") + '" data-i="' + i + '" title="' + Lab.esc(a.text || "") + '">' +
            Lab.esc((a.file || "?") + ":" + (a.line || "?")) + " " + Lab.esc(a.id) + "</button>";
        }).join("") + '</div><div class="js-snippet"></div>');
    }
    box.innerHTML = h.join("");
    box.querySelectorAll(".js-src").forEach(function (b) {
      b.addEventListener("click", function () {
        var src = d.sources[+b.dataset.i];
        Lab.show("edit", { path: src.path, focus: src.focus });
      });
    });
    var btn = box.querySelector(".js-edit");
    if (btn) btn.addEventListener("click", function () {
      Lab.show("edit", { path: edit, focus: d.criteria_key || null });
    });
    box.querySelectorAll(".anchor-chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        var a = d.anchors[+chip.dataset.i];
        box.querySelectorAll(".anchor-chip").forEach(function (c) { c.classList.remove("on"); });
        chip.classList.add("on");
        var out = box.querySelector(".js-snippet");
        if (!a.found) { out.innerHTML = '<p class="pill err">anchor text not found in ' + Lab.esc(a.file) + "</p>"; return; }
        Lab.get("/api/source", { file: a.file, line: a.line }).then(function (s) {
          out.innerHTML = '<div class="small muted">' + Lab.esc(s.path) + ":" + s.line + '</div><div class="snippet">' +
            s.lines.map(function (l) {
              return '<div class="ln' + (l.n === s.line ? " hit" : "") + '"><span class="n">' + l.n + "</span>" + Lab.esc(l.text) + "</div>";
            }).join("") + "</div>";
          var hit = out.querySelector(".hit");
          if (hit) hit.scrollIntoView({ block: "center" });
        }).catch(function (e) { out.textContent = e.message; });
      });
    });
  };
})();
