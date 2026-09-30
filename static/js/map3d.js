/* The 3D map (3d-force-graph + three-spritetext, vendored).  Fixed layout from /api/map3d:
   x = column, y = spread in the column, z = plane (READS above, CAN-PICK below, play between).
   Nodes are pinned (fx/fy/fz): no force layout ever moves them.  Same interface as
   Lab.Graph: load, fit, restyle, select, highlight(path), clearHighlight. */
"use strict";

(function () {
  Lab.load3d = function () {
    if (!Lab._load3d) {
      Lab._load3d = import("/static/js/map3d-boot.mjs").then(function (m) { return m.ready; });
      Lab._load3d.catch(function () { Lab._load3d = null; });
    }
    return Lab._load3d;
  };

  var GROUP_VAR = { input: "--c-input", prompt: "--c-input", trigger: "--c-trigger", part: "--c-part",
    question: "--c-question", option: "--c-option", action: "--c-action", manager: "--c-manager",
    queue: "--c-queue" };
  var KIND_VAR = { reads: "--c-input", picks: "--c-question", offers: "--c-part", petra: "--c-action",
    describes: "--c-describes", config: "--edge" };

  function Graph3D(container, opts) {
    this.el = container;
    this.opts = opts || {};
    this.G = null;
    this.data = null;
    this.state = null;      // {kind: "focus"|"path", ...} or null
  }

  Graph3D.prototype.load = function (data) {
    var self = this;
    return Lab.load3d().then(function () {
      self.data = data;
      self.byId = {};
      self.out = {};
      self.inn = {};
      data.nodes.forEach(function (n) { self.byId[n.id] = n; });
      data.edges.forEach(function (e) {
        (self.out[e.source] = self.out[e.source] || []).push(e.target);
        (self.inn[e.target] = self.inn[e.target] || []).push(e.source);
      });
      var nodes = data.nodes.map(function (n) {
        return { id: n.id, n: n, fx: n.pos.x, fy: n.pos.y, fz: n.pos.z, x: n.pos.x, y: n.pos.y, z: n.pos.z };
      });
      var links = data.edges.map(function (e) {
        return { source: e.source, target: e.target, id: e.id, kind: e.kind, dashed: !!e.dashed, label: e.label };
      });
      if (!self.G) self.create();
      self.G.backgroundColor(Lab.css("--bg"));
      self.resize();
      self.G.graphData({ nodes: nodes, links: links });
      self.guides();
      self.apply();
      self.home(0);
    });
  };

  Graph3D.prototype.create = function () {
    var self = this;
    var THREE = window.THREE;
    this.G = window.ForceGraph3D({ controlType: "orbit" })(this.el)
      .showNavInfo(false)
      .enableNodeDrag(false)
      .warmupTicks(0)
      .cooldownTicks(1)
      .nodeLabel(function (o) { return Lab.esc((o.n.details && o.n.details.title) || o.n.label); })
      .nodeThreeObject(function (o) { return self.sprite(o.n); })
      .linkThreeObject(function (l) {
        var mat = l.dashed ?
          new THREE.LineDashedMaterial({ dashSize: 4, gapSize: 3, transparent: true, depthWrite: false }) :
          new THREE.LineBasicMaterial({ transparent: true, depthWrite: false });
        var geo = new THREE.BufferGeometry();
        geo.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0, 0, 0, 0], 3));
        var look = self.linkLook(l);
        mat.color.set(look.color);
        mat.opacity = look.opacity;
        var line = new THREE.Line(geo, mat);
        line.renderOrder = 1;
        l.__line = line;
        return line;
      })
      .linkPositionUpdate(function (line, pos, l) {
        var a = line.geometry.attributes.position;
        a.setXYZ(0, pos.start.x, pos.start.y, pos.start.z);
        a.setXYZ(1, pos.end.x, pos.end.y, pos.end.z);
        a.needsUpdate = true;
        line.geometry.computeBoundingSphere();
        if (l.dashed) line.computeLineDistances();
        return true;
      })
      .linkDirectionalArrowLength(3.2)
      .linkDirectionalArrowRelPos(0.9)
      .linkDirectionalArrowColor(function (l) { return self.linkLook(l).color; })
      .onNodeClick(function (o) {
        self.focus(o.id);
        if (self.opts.onSelect) self.opts.onSelect(o.n);
      })
      .onBackgroundClick(function () {
        self.clearHighlight();
        if (self.opts.onClear) self.opts.onClear();
      });
    if (window.ResizeObserver) {
      new ResizeObserver(function () { self.resize(); }).observe(this.el);
    }
  };

  Graph3D.prototype.resize = function () {
    if (!this.G) return;
    var w = this.el.clientWidth, h = this.el.clientHeight;
    if (w && h) this.G.width(w).height(h);
  };

  /* -- looks ---------------------------------------------------------------- */
  Graph3D.prototype.nodeLook = function (n) {
    var st = this.state, look = {
      fill: Lab.css("--node-fill"), border: Lab.css(GROUP_VAR[n.group] || "--c-manager"),
      text: Lab.css("--text"), opacity: n.dim ? 0.55 : 1, width: n.group === "question" ? 0.9 : 0.55
    };
    if (n.bad) { look.border = Lab.css("--c-bad"); look.fill = Lab.css("--err-soft"); }
    if (!st) return look;
    var role = st.roles[n.id];
    if (!role) { look.opacity = 0.1; return look; }
    if (role === "hl") { look.border = Lab.css("--accent"); look.width = 1.2; }
    if (role === "part") { look.border = Lab.css("--hl"); look.width = 1.2; }
    if (role === "rule") { look.border = Lab.css("--warn"); look.width = 1.2; }
    if (role === "grey") { look.text = Lab.css("--muted"); look.border = Lab.css("--muted"); look.opacity = 0.8; }
    if (role === "chosen") { look.fill = Lab.css("--accent"); look.border = Lab.css("--accent"); look.text = "#ffffff"; look.width = 1.2; }
    return look;
  };

  Graph3D.prototype.linkLook = function (l) {
    var st = this.state, color = Lab.css(KIND_VAR[l.kind] || "--edge");
    var look = { color: color, opacity: l.dashed ? 0.5 : 0.6 };
    if (!st) return look;
    var role = st.links[l.id];
    if (!role) return { color: color, opacity: 0.04 };
    if (role === "hl") return { color: Lab.css("--accent"), opacity: 1 };
    if (role === "soft") return { color: Lab.css("--hl"), opacity: 0.85 };
    return { color: color, opacity: 0.75 };
  };

  /* Sprite text never wraps by itself: wrap long labels (max 3 lines of ~26 chars). */
  function wrap(label) {
    var out = [];
    String(label).split("\n").forEach(function (line) {
      var cur = "";
      line.split(" ").forEach(function (w) {
        if (cur && (cur + " " + w).length > 26) { out.push(cur); cur = w; } else cur = cur ? cur + " " + w : w;
      });
      out.push(cur);
    });
    if (out.length > 3) {
      out = out.slice(0, 3);
      out[2] = out[2].replace(/.{0,2}$/, "…");
    }
    return out.join("\n");
  }

  Graph3D.prototype.sprite = function (n) {
    var s = new window.SpriteText(wrap(n.label.replace("  (doctrine.json)", "\n(doctrine.json)")),
      n.group === "question" ? 18 : 12);
    s.fontFace = "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Arial, sans-serif";
    s.fontWeight = n.group === "question" || n.group === "prompt" ? "bold" : "normal";
    s.padding = [3, 1.6];
    s.borderRadius = 2.2;
    s.material.transparent = true;
    s.material.depthWrite = false;
    s.renderOrder = 2;
    this.style(s, n);
    return s;
  };

  Graph3D.prototype.style = function (s, n) {
    var look = this.nodeLook(n);
    s.color = look.text;
    s.backgroundColor = look.fill;
    s.borderColor = look.border;
    s.borderWidth = look.width;
    s.material.opacity = look.opacity;
  };

  Graph3D.prototype.apply = function () {
    if (!this.G) return;
    var self = this;
    var gd = this.G.graphData();
    gd.nodes.forEach(function (o) { if (o.__threeObj) self.style(o.__threeObj, o.n); });
    gd.links.forEach(function (l) {
      var look = self.linkLook(l);
      if (l.__line) {
        l.__line.material.color.set(look.color);
        l.__line.material.opacity = look.opacity;
      }
    });
    // Arrow colours follow the link colours.
    this.G.linkDirectionalArrowColor(this.G.linkDirectionalArrowColor());
  };

  /* -- planes, columns, labels ----------------------------------------------- */
  Graph3D.prototype.guides = function () {
    var THREE = window.THREE, scene = this.G.scene(), d = this.data;
    if (this.guideGroup) {
      scene.remove(this.guideGroup);
      this.guideGroup.traverse(function (o) { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
    }
    var grp = new THREE.Group();
    var line = Lab.css("--border"), muted = Lab.css("--muted");
    var tint = { reads: Lab.css("--c-input"), pick: Lab.css("--c-option") };
    d.planes.forEach(function (p) {
      var w = p.x1 - p.x0, h = p.y1 - p.y0, cx = (p.x0 + p.x1) / 2, cy = (p.y0 + p.y1) / 2;
      var geo = new THREE.PlaneGeometry(w, h);
      var mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: tint[p.id], transparent: true, opacity: 0.05,
        side: THREE.DoubleSide, depthWrite: false }));
      mesh.position.set(cx, cy, p.z);
      grp.add(mesh);
      var edge = new THREE.LineSegments(new THREE.EdgesGeometry(geo),
        new THREE.LineBasicMaterial({ color: tint[p.id], transparent: true, opacity: 0.45 }));
      edge.position.set(cx, cy, p.z);
      grp.add(edge);
      var title = new window.SpriteText(p.label, 22);
      title.color = tint[p.id];
      title.fontWeight = "bold";
      title.material.depthWrite = false;
      title.position.set(p.x0 + 200, p.y1 + 26, p.z);
      grp.add(title);
      d.columns.filter(function (c) { return c.plane === p.id; }).forEach(function (c) {
        var g2 = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(c.x, p.y0 + 6, p.z), new THREE.Vector3(c.x, p.y1 - 30, p.z)]);
        var l2 = new THREE.Line(g2, new THREE.LineBasicMaterial({ color: line, transparent: true, opacity: 0.55, depthWrite: false }));
        grp.add(l2);
        var head = new window.SpriteText(c.label.toUpperCase(), 9);
        head.color = muted;
        head.fontWeight = "bold";
        head.material.depthWrite = false;
        head.position.set(c.x, p.y1 - 16, p.z);
        grp.add(head);
      });
    });
    // Where the two flows meet: a dashed guide through play, from one plane to the other.
    var play = this.byId["q:play"];
    if (play && d.planes.length === 2) {
      var g3 = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(play.pos.x, play.pos.y, d.planes[0].z),
        new THREE.Vector3(play.pos.x, play.pos.y, d.planes[1].z)]);
      var l3 = new THREE.Line(g3, new THREE.LineDashedMaterial({ color: Lab.css("--c-question"), dashSize: 5, gapSize: 4,
        transparent: true, opacity: 0.6 }));
      l3.computeLineDistances();
      grp.add(l3);
    }
    scene.add(grp);
    this.guideGroup = grp;
  };

  /* -- camera ------------------------------------------------------------------ */
  /* Point the camera from direction `dir` at `pts`: project them on the view plane, centre
     the frame on them and back off just enough for both the width and the height. */
  Graph3D.prototype.aim = function (pts, dir, ms) {
    if (!this.G || !pts.length) return;
    var len = Math.hypot(dir.x, dir.y, dir.z) || 1;
    var f = { x: dir.x / len, y: dir.y / len, z: dir.z / len };            // target -> camera
    var right = { x: f.z, y: 0, z: -f.x };                                   // worldUp x f
    var rl = Math.hypot(right.x, right.z) || 1;
    right.x /= rl; right.z /= rl;
    var up = { x: f.y * right.z - f.z * right.y, y: f.z * right.x - f.x * right.z, z: f.x * right.y - f.y * right.x };
    var dot = function (a, b) { return a.x * b.x + a.y * b.y + a.z * b.z; };
    var c0 = { x: 0, y: 0, z: 0 };
    pts.forEach(function (p) { c0.x += p.x / pts.length; c0.y += p.y / pts.length; c0.z += p.z / pts.length; });
    var xs = [], ys = [], zs = [];
    pts.forEach(function (p) {
      var r = { x: p.x - c0.x, y: p.y - c0.y, z: p.z - c0.z };
      xs.push(dot(r, right)); ys.push(dot(r, up)); zs.push(dot(r, f));
    });
    var mx = (Math.min.apply(null, xs) + Math.max.apply(null, xs)) / 2;
    var my = (Math.min.apply(null, ys) + Math.max.apply(null, ys)) / 2;
    var c = { x: c0.x + right.x * mx + up.x * my, y: c0.y + right.y * mx + up.y * my, z: c0.z + right.z * mx + up.z * my };
    var cam = this.G.camera();
    var tv = Math.tan(cam.fov * Math.PI / 360), th = tv * (cam.aspect || 1.4);
    var d = 50;
    // Nodes are labels, not points: leave room for half a label around each centre.
    for (var i = 0; i < pts.length; i++) {
      d = Math.max(d, zs[i] + (Math.abs(xs[i] - mx) + 110) / th, zs[i] + (Math.abs(ys[i] - my) + 30) / tv);
    }
    d *= 1.03;
    this.G.cameraPosition({ x: c.x + f.x * d, y: c.y + f.y * d, z: c.z + f.z * d }, c, ms === undefined ? 600 : ms);
  };

  /* Default view: in front, to the right and above, so the READS plane (front) and the
     CAN-PICK plane (behind) both show, with every node and plane in the frame. */
  Graph3D.prototype.home = function (ms) {
    if (!this.G || !this.data) return;
    var cam = this.G.camera();
    if (cam.fov !== 28) { cam.fov = 28; cam.updateProjectionMatrix(); }
    var yaw = this.yaw === undefined ? 0.6 : this.yaw, pitch = this.pitch === undefined ? 0.42 : this.pitch;
    var pts = this.data.nodes.map(function (n) { return n.pos; });
    this.data.planes.forEach(function (p) {
      pts.push({ x: p.x0, y: p.y0, z: p.z }, { x: p.x1, y: p.y1, z: p.z }, { x: p.x0, y: p.y1, z: p.z }, { x: p.x1, y: p.y0, z: p.z });
    });
    this.aim(pts, { x: Math.sin(yaw) * Math.cos(pitch), y: Math.sin(pitch), z: Math.cos(yaw) * Math.cos(pitch) }, ms);
  };

  /* Keep the viewing direction, frame a set of node ids. */
  Graph3D.prototype.frame = function (ids, ms) {
    if (!this.G || !this.byId) return;
    var self = this;
    var pts = ids.map(function (i) { return self.byId[i] && self.byId[i].pos; }).filter(Boolean);
    var cam = this.G.camera().position, t = this.G.controls().target;
    this.aim(pts, { x: cam.x - t.x, y: cam.y - t.y, z: cam.z - t.z }, ms);
  };

  Graph3D.prototype.fit = function () {
    this.resize();
    this.home();
  };

  Graph3D.prototype.restyle = function () {
    if (!this.G || !this.data) return;
    this.G.backgroundColor(Lab.css("--bg"));
    this.guides();
    this.apply();
  };

  /* -- focus: a node's whole upstream and downstream ------------------------------ */
  Graph3D.prototype.reach = function (id, adj) {
    var seen = {}, todo = [id];
    while (todo.length) {
      var x = todo.pop();
      (adj[x] || []).forEach(function (y) { if (!seen[y]) { seen[y] = true; todo.push(y); } });
    }
    delete seen[id];
    return seen;
  };

  Graph3D.prototype.focus = function (id) {
    if (!this.byId || !this.byId[id]) return;
    var up = this.reach(id, this.inn), down = this.reach(id, this.out);
    var roles = {}, links = {};
    Object.keys(up).forEach(function (k) { roles[k] = "on"; });
    Object.keys(down).forEach(function (k) { roles[k] = "on"; });
    roles[id] = "hl";
    this.data.edges.forEach(function (e) {
      var a = e.source, b = e.target;
      var upPath = (up[a] || a === id) && (up[b] || b === id);
      var downPath = (down[a] || a === id) && (down[b] || b === id);
      if (upPath || downPath) links[e.id] = (a === id || b === id) ? "hl" : "on";
    });
    this.state = { kind: "focus", id: id, roles: roles, links: links, up: up, down: down };
    this.apply();
    this.frame(Object.keys(roles));
  };

  Graph3D.prototype.select = function (id) {
    this.focus(id);
  };

  Graph3D.prototype.clearHighlight = function () {
    this.state = null;
    this.apply();
  };

  /* The timeline's path: parts asked, options offered (grey), the chosen one bright,
     the rule's pick outlined, the Petra action and managers lit. */
  Graph3D.prototype.highlight = function (path) {
    if (!this.byId) return;
    if (!path) { this.clearHighlight(); return; }
    var self = this, roles = {}, links = {};
    ["q:play", "prompt"].forEach(function (id) { roles[id] = "on"; });
    this.data.nodes.forEach(function (n) { if (n.group === "input") roles[n.id] = "on"; });
    (path.parts || []).forEach(function (id) {
      roles[id] = "part";
      var trig = "trig:" + id.split(":")[1];
      if (self.byId[trig]) roles[trig] = "part";
      if (id.indexOf("in:") === 0) roles[id] = "part";
    });
    (path.offered || []).forEach(function (id) { roles[id] = "grey"; });
    if (path.rule && path.rule !== path.chosen) roles[path.rule] = "rule";
    if (path.chosen) roles[path.chosen] = "chosen";
    var managers = (path.managers || []).slice();
    if (path.action) {
      roles[path.action] = "hl";
      if (!managers.length) managers = (this.out[path.action] || []).slice();
    }
    managers.forEach(function (id) { roles[id] = "hl"; });
    this.data.edges.forEach(function (e) {
      var a = e.source, b = e.target;
      if (e.kind === "reads" && roles[a] && roles[b]) links[e.id] = "on";
      else if (a === "q:play" && b === path.chosen) links[e.id] = "hl";
      else if (a === "q:play" && roles[b] === "grey") links[e.id] = "soft";
      else if (a === path.chosen && b === path.action) links[e.id] = "hl";
      else if (a === path.action && managers.indexOf(b) >= 0) links[e.id] = "hl";
      else if ((roles[a] === "part") && (roles[b] === "part" || roles[b] === "grey" || roles[b] === "chosen" || roles[b] === "rule")) links[e.id] = "soft";
    });
    this.state = { kind: "path", roles: roles, links: links };
    this.apply();
    if (this.opts.zoomToPath) this.frame(Object.keys(roles).filter(function (k) { return roles[k] !== "on"; }));
  };

  Lab.Graph3D = Graph3D;
})();
