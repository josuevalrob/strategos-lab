/* L5 + L8: edit the Q&A files (forms + raw JSON), Apply = write + git commit of that file only,
   history / diff / restore, and "+" clone of a civ. */
"use strict";

(function () {
  var V = Lab.views.edit = {
    files: null, path: null, doc: null, obj: null, text: null, mode: "form",
    validation: null, vTimer: null, focus: null, history: []
  };

  function draftKey(path) { return "lab.draft:" + path; }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

  V.init = function () {
    document.querySelectorAll("#edit-mode button").forEach(function (b) {
      b.addEventListener("click", function () { V.setMode(b.dataset.mode); });
    });
    document.getElementById("edit-discard").addEventListener("click", V.discard);
    document.getElementById("edit-diff").addEventListener("click", V.showDiff);
    document.getElementById("edit-apply").addEventListener("click", V.applyFlow);
    V.mode = Lab.store.get("lab.edit.mode", "form");
  };

  V.shown = function (arg) {
    V.loadFiles().then(function () {
      var path = (arg && arg.path) || V.path || Lab.store.get("lab.edit.path", null) ||
        (V.files && V.files.questions.length ? V.files.questions.filter(function (q) { return q.name === "play"; })[0].path : null);
      V.focus = arg && arg.focus ? arg.focus : null;
      if (arg && arg.focus) V.mode = "form";
      if (path) V.open(path, !!(arg && arg.path));
    });
    V.onInfo(Lab.info);
  };

  V.onInfo = function (info) {
    var box = document.getElementById("edit-live-note");
    if (!box || !info) return;
    var doctrine = V.doc && V.doc.kind === "doctrine";
    var note = info.live_note ? info.live_note + (doctrine && V.doc.choices ? " " + V.doc.choices.note : "") : "";
    box.textContent = note;
    box.classList.toggle("hidden", !note);
  };

  /* -- file list ------------------------------------------------------------- */
  V.loadFiles = function () {
    return Lab.get("/api/files").then(function (f) {
      V.files = f;
      V.renderFiles();
    }).catch(function (e) { Lab.toast("Files: " + e.message, true); });
  };

  V.renderFiles = function () {
    var f = V.files;
    if (!f) return;
    function item(path, label) {
      var git = f.status[path];
      var draft = Lab.store.get(draftKey(path), null);
      return '<div class="f' + (path === V.path ? " on" : "") + '" data-path="' + Lab.esc(path) + '" title="' + Lab.esc(path) + '">' +
        Lab.esc(label) + (draft ? '<span class="m draft" title="unsaved draft">● draft</span>' : "") +
        (git ? '<span class="m git" title="uncommitted change in git: ' + Lab.esc(git) + '">' + Lab.esc(git.trim()) + "</span>" : "") + "</div>";
    }
    var h = ["<h4>Questions</h4>"];
    f.questions.forEach(function (q) { h.push(item(q.path, q.name + ".json")); });
    if ((f.doctrine || []).length) {
      h.push("<h4>Doctrine</h4>");
      f.doctrine.forEach(function (x) { h.push(item(x.path, "doctrine.json")); });
    }
    h.push("<h4>Civs</h4>");
    f.civs.forEach(function (c) { h.push(item(c.path, c.civ + ".json")); });
    h.push('<div class="clone"><button class="btn tiny js-clone">+ New civ from…</button></div>');
    Object.keys(f.heroes).forEach(function (civ) {
      h.push("<h4>Heroes · " + Lab.esc(civ) + "</h4>");
      f.heroes[civ].forEach(function (x) { h.push(item(x.path, x.name + ".json")); });
    });
    var box = document.getElementById("edit-files");
    box.innerHTML = h.join("");
    box.querySelectorAll(".f").forEach(function (el) {
      el.addEventListener("click", function () { V.open(el.dataset.path, true); });
    });
    box.querySelector(".js-clone").addEventListener("click", function () {
      Lab.cloneFlow((V.path && V.doc && V.doc.choices && V.doc.choices.civ) || "spart");
    });
  };

  /* -- open / draft ------------------------------------------------------------- */
  V.open = function (path, userAction) {
    return Lab.get("/api/file", { path: path }).then(function (doc) {
      V.path = path;
      V.doc = doc;
      Lab.store.set("lab.edit.path", path);
      var d = Lab.store.get(draftKey(path), null);
      V.stale = false;
      if (d && d.base !== doc.sha256) V.stale = true;
      if (d) V.mode = d.mode === "raw" ? "raw" : "form";   // reopen a draft in the mode it was made in
      if (d && d.mode === "raw") { V.text = d.text; V.obj = null; try { V.obj = JSON.parse(d.text); } catch (e) { /* stays raw */ } }
      else if (d && d.data !== undefined) { V.obj = d.data; V.text = JSON.stringify(d.data, null, 2); }
      else { V.obj = doc.data ? clone(doc.data) : null; V.text = doc.text; }
      if (V.obj === null && V.mode === "form") V.mode = "raw";
      document.getElementById("edit-title").textContent = path.split("/").slice(-3).join("/");
      V.renderFiles();
      V.render();
      V.loadHistory();
      V.validate();
      V.onInfo(Lab.info);
    }).catch(function (e) { Lab.toast("Open: " + e.message, true); });
  };

  V.isDraft = function () {
    if (!V.doc) return false;
    if (V.mode === "raw") return V.text !== V.doc.text;
    return V.obj !== null && !same(V.obj, V.doc.data);
  };

  V.saveDraft = function () {
    if (!V.path) return;
    if (V.isDraft()) {
      Lab.store.set(draftKey(V.path), V.mode === "raw" ?
        { mode: "raw", text: V.text, base: V.doc.sha256 } : { mode: "form", data: V.obj, base: V.doc.sha256 });
    } else {
      Lab.store.del(draftKey(V.path));
    }
    V.updateButtons();
    V.renderFiles();
    clearTimeout(V.vTimer);
    V.vTimer = setTimeout(V.validate, 350);
  };

  V.updateButtons = function () {
    var draft = V.isDraft();
    document.getElementById("edit-discard").disabled = !draft;
    document.getElementById("edit-diff").disabled = !draft;
    var hasErr = V.validation && V.validation.errors && V.validation.errors.length;
    document.getElementById("edit-apply").disabled = !draft || !!hasErr || V.rawInvalid;
    var st = document.getElementById("edit-state");
    var git = V.doc && V.doc.status ? " · uncommitted in git (" + V.doc.status.trim() + ")" : "";
    st.textContent = (draft ? "draft: not applied" : "no changes") + git + (V.stale ? " · the file changed on disk since this draft" : "");
  };

  V.discard = function () {
    Lab.store.del(draftKey(V.path));
    V.obj = V.doc.data ? clone(V.doc.data) : null;
    V.text = V.doc.text;
    V.stale = false;
    V.render();
    V.saveDraft();
  };

  V.setMode = function (mode) {
    if (mode === V.mode) return;
    if (mode === "form") {
      try { V.obj = JSON.parse(V.text); } catch (e) { Lab.toast("Fix the JSON before switching to the form: " + e.message, true); return; }
      V.finishMode(mode);
    } else if (V.obj === null || same(V.obj, V.doc.data)) {
      V.text = V.obj === null ? V.text : V.doc.text;     // unchanged: the file's own bytes
      V.finishMode(mode);
    } else {
      // The exact text Apply would write (the file's style, its numbers as written).
      Lab.post("/api/format", { path: V.path, data: V.obj }).then(function (r) {
        V.text = r.text;
        V.finishMode(mode);
      }).catch(function (e) { Lab.toast(e.message, true); });
    }
  };

  V.finishMode = function (mode) {
    V.mode = mode;
    Lab.store.set("lab.edit.mode", mode);
    V.render();
    V.saveDraft();
  };

  V.payload = function () {
    if (V.mode === "raw") return { path: V.path, text: V.text };
    return { path: V.path, data: V.obj };
  };

  V.validate = function () {
    if (!V.path) return;
    var box = document.getElementById("edit-validation");
    if (V.mode === "raw") {
      try { JSON.parse(V.text); V.rawInvalid = false; } catch (e) {
        V.rawInvalid = true;
        box.innerHTML = '<div class="v err">Invalid JSON: ' + Lab.esc(e.message) + "</div>";
        V.updateButtons();
        return;
      }
    }
    Lab.post("/api/validate", V.payload()).then(function (res) {
      V.validation = res;
      var h = [];
      (res.errors || []).forEach(function (e) { h.push('<div class="v err">✕ ' + Lab.esc(e) + "</div>"); });
      (res.warnings || []).forEach(function (w) { h.push('<div class="v warn">! ' + Lab.esc(w) + "</div>"); });
      if (!h.length && V.isDraft()) h.push('<div class="v ok">✓ valid: every option is one the game can offer</div>');
      box.innerHTML = h.join("");
      V.updateButtons();
    }).catch(function (e) {
      box.innerHTML = '<div class="v err">' + Lab.esc(e.message) + "</div>";
    });
  };

  /* -- rendering ------------------------------------------------------------------ */
  V.render = function () {
    document.querySelectorAll("#edit-mode button").forEach(function (b) { b.classList.toggle("on", b.dataset.mode === V.mode); });
    var body = document.getElementById("edit-body");
    body.innerHTML = "";
    if (!V.doc) return;
    if (V.mode === "raw" || V.obj === null) {
      var ta = Lab.el('<textarea class="mono" spellcheck="false" style="min-height:70vh"></textarea>');
      ta.value = V.text || "";
      ta.addEventListener("input", function () { V.text = ta.value; V.saveDraft(); });
      body.appendChild(ta);
    } else if (V.doc.kind === "doctrine") {
      V.doctrineForm(body);
    } else if (V.doc.kind === "question") {
      V.questionForm(body);
    } else if (V.doc.kind === "civ") {
      V.civForm(body);
    } else {
      V.heroForm(body);
    }
    V.updateButtons();
    if (V.focus) {
      var row = body.querySelector('[data-key="' + CSS.escape(V.focus) + '"]');
      if (row) { row.classList.add("focus"); row.scrollIntoView({ block: row.tagName === "FIELDSET" ? "start" : "center" }); var t = row.querySelector(".crit-row textarea, :scope > textarea"); if (t) t.focus(); }
      V.focus = null;
    }
  };

  function field(label, input, help) {
    var row = Lab.el('<div class="row"><label>' + Lab.esc(label) + '</label><div class="grow"></div></div>');
    row.querySelector(".grow").appendChild(input);
    if (help) row.querySelector(".grow").appendChild(Lab.el('<div class="small muted">' + help + "</div>"));
    return row;
  }

  function textInput(value, onChange, mono) {
    var i = Lab.el('<input type="text"' + (mono ? ' class="mono"' : "") + ' style="width:100%">');
    i.value = value === undefined || value === null ? "" : value;
    i.addEventListener("input", function () { onChange(i.value); });
    return i;
  }

  function textArea(value, onChange, rows) {
    var t = Lab.el('<textarea rows="' + (rows || 2) + '"></textarea>');
    t.value = value || "";
    t.addEventListener("input", function () { onChange(t.value); });
    return t;
  }

  function select(options, value, onChange, allowEmpty) {
    var s = Lab.el("<select></select>");
    var opts = (allowEmpty ? [["", "(none)"]] : []).concat(options.map(function (o) { return Array.isArray(o) ? o : [o, o]; }));
    if (value && !opts.some(function (o) { return o[0] === value; })) opts.push([value, value + " (unknown)"]);
    s.innerHTML = opts.map(function (o) {
      return '<option value="' + Lab.esc(o[0]) + '"' + (o[0] === (value || "") ? " selected" : "") + ">" + Lab.esc(o[1]) + "</option>";
    }).join("");
    s.addEventListener("change", function () { onChange(s.value || undefined); });
    return s;
  }

  /* A list of text lines: strings or {text, noAction}. */
  function linesEditor(lines, onChange) {
    var box = Lab.el("<div></div>");
    function draw() {
      box.innerHTML = "";
      lines.forEach(function (line, i) {
        var isObj = line && typeof line === "object";
        var row = Lab.el('<div class="line-row"><span class="n">' + (i + 1) + '</span><div class="grow"></div>' +
          '<div class="mini-btns"><button class="btn tiny" title="up">↑</button><button class="btn tiny" title="down">↓</button>' +
          '<button class="btn tiny danger" title="remove">✕</button></div></div>');
        var t = textArea(isObj ? line.text : line, function (v) {
          if (isObj) lines[i].text = v; else lines[i] = v;
          onChange();
        }, Math.max(2, Math.ceil(String(isObj ? line.text : line).length / 110)));
        row.querySelector(".grow").appendChild(t);
        if (isObj) {
          var na = textInput(line.noAction, function (v) { lines[i].noAction = v; onChange(); });
          na.placeholder = "noAction: why no JS action carries this line out";
          row.querySelector(".grow").appendChild(na);
        }
        var b = row.querySelectorAll("button");
        b[0].addEventListener("click", function (e) { e.preventDefault(); if (i > 0) { var x = lines[i]; lines[i] = lines[i - 1]; lines[i - 1] = x; draw(); onChange(); } });
        b[1].addEventListener("click", function (e) { e.preventDefault(); if (i < lines.length - 1) { var x = lines[i]; lines[i] = lines[i + 1]; lines[i + 1] = x; draw(); onChange(); } });
        b[2].addEventListener("click", function (e) { e.preventDefault(); lines.splice(i, 1); draw(); onChange(); });
        box.appendChild(row);
      });
      var add = Lab.el('<button class="btn tiny">+ add line</button>');
      add.addEventListener("click", function (e) { e.preventDefault(); lines.push(""); draw(); onChange(); });
      box.appendChild(add);
    }
    draw();
    return box;
  }

  /* A JSON sub-object editor (the params not covered by the form). */
  function jsonBox(value, onChange) {
    var wrap = Lab.el('<div></div>');
    var t = Lab.el('<textarea class="mono" spellcheck="false"></textarea>');
    t.value = JSON.stringify(value || {}, null, 2);
    t.rows = Math.min(30, Math.max(4, t.value.split("\n").length));
    var err = Lab.el('<div class="small" style="color:var(--err)"></div>');
    t.addEventListener("input", function () {
      try { var v = JSON.parse(t.value); err.textContent = ""; V.rawInvalid = false; onChange(v); }
      catch (e) { err.textContent = "Invalid JSON: " + e.message + " (not applied to the draft yet)"; V.rawInvalid = true; V.updateButtons(); }
    });
    wrap.appendChild(t);
    wrap.appendChild(err);
    return wrap;
  }

  /* The rest of an object (keys the form does not handle) as JSON, key order kept. */
  function restBox(obj, handled) {
    var rest = {};
    Object.keys(obj).forEach(function (k) { if (handled.indexOf(k) < 0) rest[k] = obj[k]; });
    return jsonBox(rest, function (v) {
      var order = Object.keys(obj), cur = {};
      order.forEach(function (k) { cur[k] = obj[k]; delete obj[k]; });
      order.forEach(function (k) {
        if (handled.indexOf(k) >= 0) obj[k] = cur[k];
        else if (k in v) obj[k] = v[k];
      });
      Object.keys(v).forEach(function (k) { if (!(k in obj)) obj[k] = v[k]; });
      V.saveDraft();
    });
  }

  /* doctrine.json: each stratagem's order list = the military play options. */
  V.doctrineForm = function (body) {
    var o = V.obj, ch = V.doc.choices || {};
    var allowedBy = ch.stratagem_orders || {}, routes = ch.order_routes || {}, crit = ch.play_criteria || {};
    var usedBy = ch.used_by || {};
    body.appendChild(Lab.el('<div class="note info" style="margin:0 0 14px">' +
      "Each stratagem's <b>orders</b> are the military options of the play question for a civ (or hero) " +
      "playing it, in this order. Only orders head.js lets that stratagem issue can be added " +
      "(STRATAGEM_ORDERS / ORDER_ROUTES). " + Lab.esc(ch.note || "") +
      " The other sections (live, civs rows, about) are edited in Raw JSON.</div>"));
    Object.keys(o.stratagems || {}).forEach(function (kind) {
      var st = o.stratagems[kind];
      var fs = Lab.el('<fieldset data-key="stratagem:' + Lab.esc(kind) + '"><legend>stratagem: ' + Lab.esc(kind) + "</legend></fieldset>");
      fs.appendChild(field("played by", Lab.el('<div class="small" style="padding-top:6px">' +
        ((usedBy[kind] || []).map(Lab.esc).join("<br>") || '<span class="muted">nobody</span>') + "</div>")));
      st.orders = Array.isArray(st.orders) ? st.orders : [];
      var list = Lab.el("<div></div>");
      var allowed = allowedBy[kind];
      function draw() {
        list.innerHTML = "";
        st.orders.forEach(function (ord, i) {
          var ok = allowed ? allowed.indexOf(ord) >= 0 : false;
          var row = Lab.el('<div class="order-row"><span class="n">' + (i + 1) + '</span><code>' + Lab.esc(ord) + "</code>" +
            '<span class="small ' + (ok ? "muted" : "") + '" style="' + (ok ? "" : "color:var(--err)") + '">' +
            (ok ? "&rarr; " + Lab.esc((routes[ord] || []).join(", ") || "no manager") +
              (crit[ord] ? " · reads: “" + Lab.esc(crit[ord]) + "”" : " · <b>no wording in play.json</b>") :
              "head.js does not let " + Lab.esc(kind) + " issue this order") + "</span>" +
            '<span class="mini-btns"><button class="btn tiny" title="earlier">↑</button><button class="btn tiny danger" title="remove">✕</button></span></div>');
          var b = row.querySelectorAll("button");
          b[0].addEventListener("click", function (e) { e.preventDefault(); if (i > 0) { var x = st.orders[i]; st.orders[i] = st.orders[i - 1]; st.orders[i - 1] = x; draw(); V.saveDraft(); } });
          b[1].addEventListener("click", function (e) { e.preventDefault(); st.orders.splice(i, 1); draw(); V.saveDraft(); });
          list.appendChild(row);
        });
        var free = (allowed || []).filter(function (x) { return st.orders.indexOf(x) < 0; });
        var add = Lab.el('<div class="order-row"><span></span><select class="mono"></select><span class="small muted">' +
          (allowed ? "head.js lets " + Lab.esc(kind) + " issue: " + Lab.esc(allowed.join(", ")) : "head.js has no stratagem " + Lab.esc(kind)) +
          '</span><button class="btn tiny">+ add</button></div>');
        add.querySelector("select").innerHTML = free.map(function (x) { return "<option>" + Lab.esc(x) + "</option>"; }).join("");
        add.querySelector("select").disabled = add.querySelector("button").disabled = !free.length;
        add.querySelector("button").addEventListener("click", function (e) {
          e.preventDefault();
          var v = add.querySelector("select").value;
          if (v && st.orders.indexOf(v) < 0) { st.orders.push(v); draw(); V.saveDraft(); }
        });
        list.appendChild(add);
      }
      draw();
      fs.appendChild(field("orders", list, "The options offered for this stratagem (strike only when something could reach the choke)."));
      if ("needsChoke" in st) {
        var cb = Lab.el('<label class="check"><input type="checkbox"' + (st.needsChoke ? " checked" : "") + "> needs a pass (asked as pass_order; else play_order)</label>");
        cb.querySelector("input").addEventListener("change", function (e) { st.needsChoke = e.target.checked; V.saveDraft(); });
        fs.appendChild(field("needsChoke", cb));
      }
      fs.appendChild(field("params, rule …", restBox(st, ["orders", "needsChoke"])));
      body.appendChild(fs);
    });
  };

  V.questionForm = function (body) {
    var o = V.obj, ch = V.doc.choices || {};
    var top = Lab.el('<fieldset><legend>File</legend></fieldset>');
    top.appendChild(field("stratagem", Lab.el('<div class="mono" style="padding-top:6px">' + Lab.esc(o.stratagem || "-") + "</div>")));
    ["v", "wording_round"].forEach(function (k) {
      if (o[k] === undefined) return;
      top.appendChild(field(k, textInput(o[k], function (v) { o[k] = /^\d+$/.test(v) ? +v : v; V.saveDraft(); }, true)));
    });
    if (o.context && typeof o.context === "object") {
      top.appendChild(field("context suffix", textArea(o.context.suffix, function (v) { o.context.suffix = v; V.saveDraft(); }, 2),
        "Added under the state text the model reads."));
    }
    if (Array.isArray(o._comment)) {
      top.appendChild(field("_comment", Lab.el('<div class="small muted">' + o._comment.map(Lab.esc).join(" ") + "</div>")));
    }
    body.appendChild(top);
    Object.keys(o.questions || {}).forEach(function (qid) {
      var q = o.questions[qid];
      var fs = Lab.el('<fieldset><legend>question: ' + Lab.esc(qid) + "</legend></fieldset>");
      fs.appendChild(field("instructions", textArea(q.instructions, function (v) { q.instructions = v; V.saveDraft(); }, 2),
        "What the model is asked (goes with the criteria below)."));
      var crit = q.criteria = q.criteria || {};
      var list = Lab.el("<div></div>");
      var known = ((ch.known_options || {})[qid]) || [];
      function draw() {
        list.innerHTML = '<div class="small muted" style="margin:6px 0">Options &rarr; what the model reads for each' +
          (o.criteria_by_prefix ? ' (a key ending in ":" describes every option with that prefix; {param} is filled in)' : "") + "</div>";
        Object.keys(crit).forEach(function (key) {
          var row = Lab.el('<div class="crit-row" data-key="' + Lab.esc(key) + '"></div>');
          var k = textInput(key, function () { }, true);
          k.addEventListener("change", function () {
            var nk = k.value.trim();
            if (!nk || nk === key) { k.value = key; return; }
            if (crit[nk] !== undefined) { Lab.toast("There is already an option " + nk, true); k.value = key; return; }
            var rebuilt = {};
            Object.keys(crit).forEach(function (x) { rebuilt[x === key ? nk : x] = crit[x]; });
            q.criteria = crit = rebuilt;
            draw(); V.saveDraft();
          });
          row.appendChild(k);
          row.appendChild(textArea(crit[key], function (v) { crit[key] = v; V.saveDraft(); }, 1));
          var del = Lab.el('<button class="btn tiny danger" title="remove this option\'s wording">✕</button>');
          del.addEventListener("click", function (e) { e.preventDefault(); delete crit[key]; draw(); V.saveDraft(); });
          row.appendChild(del);
          list.appendChild(row);
        });
        var missing = known.filter(function (x) { return crit[x] === undefined; });
        var add = Lab.el('<div class="crit-row"><select class="mono"></select><span class="small muted" style="padding-top:6px">' +
          (missing.length ? "options the game can offer that have no wording here" : "every known option has wording") +
          '</span><button class="btn tiny">+ add</button></div>');
        add.querySelector("select").innerHTML = missing.map(function (x) { return "<option>" + Lab.esc(x) + "</option>"; }).join("") +
          '<option value="__other">other…</option>';
        add.querySelector("button").addEventListener("click", function (e) {
          e.preventDefault();
          var v = add.querySelector("select").value;
          if (v === "__other") v = (prompt("Option key (e.g. hero: or tower:gate)") || "").trim();
          if (!v || crit[v] !== undefined) return;
          crit[v] = "";
          draw(); V.saveDraft();
        });
        list.appendChild(add);
      }
      draw();
      fs.appendChild(list);
      body.appendChild(fs);
    });
  };

  V.civForm = function (body) {
    var o = V.obj, ch = V.doc.choices || {};
    o.params = o.params || {};
    var p = o.params;
    var fs = Lab.el('<fieldset><legend>Civ (the whole file is the model\'s prompt, except params.heroOrder)</legend></fieldset>');
    fs.appendChild(field("civ", Lab.el('<div class="mono" style="padding-top:6px">' + Lab.esc(o.civ) + "</div>")));
    fs.appendChild(field("name", textInput(o.name, function (v) { o.name = v; V.saveDraft(); })));
    body.appendChild(fs);

    var tx = Lab.el("<fieldset><legend>Text: what the civ does (one line each)</legend></fieldset>");
    o.text = o.text || [];
    tx.appendChild(linesEditor(o.text, V.saveDraft));
    body.appendChild(tx);

    var hs = Lab.el('<fieldset><legend>Heroes (their files go into the prompt, in this order)</legend><div class="chk-list"></div></fieldset>');
    o.heroes = o.heroes || [];
    var hl = hs.querySelector(".chk-list");
    function drawHeroes() {
      hl.innerHTML = "";
      var all = o.heroes.concat((ch.hero_files || []).filter(function (h) { return o.heroes.indexOf(h) < 0; }));
      all.forEach(function (h) {
        var on = o.heroes.indexOf(h) >= 0;
        var l = Lab.el('<label><input type="checkbox"' + (on ? " checked" : "") + "> " + Lab.esc(h) +
          (on ? ' <button class="btn tiny" title="earlier">↑</button>' : "") + "</label>");
        l.querySelector("input").addEventListener("change", function (e) {
          if (e.target.checked) o.heroes.push(h); else o.heroes.splice(o.heroes.indexOf(h), 1);
          drawHeroes(); V.saveDraft();
        });
        var up = l.querySelector("button");
        if (up) up.addEventListener("click", function (e) {
          e.preventDefault();
          var i = o.heroes.indexOf(h);
          if (i > 0) { o.heroes.splice(i, 1); o.heroes.splice(i - 1, 0, h); drawHeroes(); V.saveDraft(); }
        });
        hl.appendChild(l);
      });
    }
    drawHeroes();
    body.appendChild(hs);

    var strats = ch.stratagems || {};
    var sopts = Object.keys(strats).map(function (k) { return [k, k + "  (" + strats[k].join(", ") + ")"]; });
    var pb = Lab.el("<fieldset><legend>Playbook: which stratagem, so which military play options (hold / strike / fallback …)</legend></fieldset>");
    pb.appendChild(field("params.doctrine", select(sopts, p.doctrine, function (v) { p.doctrine = v; V.saveDraft(); }),
      "With a pass on the map. Its orders are the play options of the pass part."));
    pb.appendChild(field("params.withoutChoke", select(sopts, p.withoutChoke, function (v) {
      if (v === undefined) delete p.withoutChoke; else p.withoutChoke = v; V.saveDraft();
    }, true), "On a map without a pass."));
    body.appendChild(pb);

    var ho = p.heroOrder;
    var hf = Lab.el("<fieldset><legend>Hero order: code's fallback when the model is silent (not in the prompt)</legend></fieldset>");
    if (ho) {
      hf.appendChild(field("building", textInput(ho.building, function (v) { ho.building = v; V.saveDraft(); }, true),
        "{civ} is replaced by the civ code."));
      ho.order = ho.order || [];
      var ol = Lab.el("<div></div>");
      var dl = "hero-tpl-" + Math.random().toString(36).slice(2);
      function drawOrder() {
        ol.innerHTML = '<datalist id="' + dl + '">' + (ch.hero_templates || []).map(function (t) { return '<option value="' + Lab.esc(t) + '">'; }).join("") + "</datalist>";
        ho.order.forEach(function (t, i) {
          var row = Lab.el('<div class="line-row"><span class="n">' + (i + 1) + '</span><div class="grow"></div><div class="mini-btns">' +
            '<button class="btn tiny">↑</button><button class="btn tiny danger">✕</button></div></div>');
          var inp = textInput(t, function (v) { ho.order[i] = v; V.saveDraft(); }, true);
          inp.setAttribute("list", dl);
          row.querySelector(".grow").appendChild(inp);
          var b = row.querySelectorAll("button");
          b[0].addEventListener("click", function (e) { e.preventDefault(); if (i > 0) { var x = ho.order[i]; ho.order[i] = ho.order[i - 1]; ho.order[i - 1] = x; drawOrder(); V.saveDraft(); } });
          b[1].addEventListener("click", function (e) { e.preventDefault(); ho.order.splice(i, 1); drawOrder(); V.saveDraft(); });
          ol.appendChild(row);
        });
        var add = Lab.el('<button class="btn tiny">+ add hero template</button>');
        add.addEventListener("click", function (e) { e.preventDefault(); ho.order.push(""); drawOrder(); V.saveDraft(); });
        ol.appendChild(add);
      }
      drawOrder();
      hf.appendChild(field("order", ol, "The heroes the game can train, in the rule's order. Each becomes a play option hero:&lt;name&gt;."));
      if (ho.urgent) {
        hf.appendChild(field("urgent.hero", select(ho.order.map(function (t) { return t; }), ho.urgent.hero, function (v) { ho.urgent.hero = v; V.saveDraft(); })));
      }
    } else {
      hf.appendChild(Lab.el('<p class="muted small">No params.heroOrder: hero_next is never asked for this civ. Add it in the raw JSON.</p>'));
    }
    body.appendChild(hf);

    var rest = {};
    Object.keys(p).forEach(function (k) { if (["doctrine", "withoutChoke", "heroOrder"].indexOf(k) < 0) rest[k] = p[k]; });
    var rf = Lab.el("<fieldset><legend>Other params (JSON): trigger, force, ground, breakoff, rule, city, decline, towerSite, garrisonNow, raid …</legend></fieldset>");
    rf.appendChild(jsonBox(rest, function (v) {
      Object.keys(p).forEach(function (k) { if (["doctrine", "withoutChoke", "heroOrder"].indexOf(k) < 0) delete p[k]; });
      var keep = {};
      ["doctrine", "withoutChoke"].forEach(function (k) { if (p[k] !== undefined) keep[k] = p[k]; });
      // Keep the file's key order: doctrine / withoutChoke first, the rest, heroOrder where it was.
      var order = Object.keys(V.doc.data.params || {});
      var merged = {};
      order.forEach(function (k) {
        if (k in keep) merged[k] = keep[k];
        else if (k === "heroOrder" && p.heroOrder) merged[k] = p.heroOrder;
        else if (k in v) merged[k] = v[k];
      });
      Object.keys(keep).forEach(function (k) { if (!(k in merged)) merged[k] = keep[k]; });
      Object.keys(v).forEach(function (k) { if (!(k in merged)) merged[k] = v[k]; });
      if (p.heroOrder && !("heroOrder" in merged)) merged.heroOrder = p.heroOrder;
      o.params = merged;
      p = merged;
      V.saveDraft();
    }));
    body.appendChild(rf);
  };

  V.heroForm = function (body) {
    var o = V.obj, ch = V.doc.choices || {};
    o.params = o.params || {};
    var fs = Lab.el("<fieldset><legend>Hero (this whole file is in the model's prompt)</legend></fieldset>");
    fs.appendChild(field("hero", textInput(o.hero, function (v) { o.hero = v; V.saveDraft(); })));
    fs.appendChild(field("battle", textInput(o.battle, function (v) { o.battle = v; V.saveDraft(); })));
    o.templates = o.templates || [];
    var dl = "hero-tpl-" + Math.random().toString(36).slice(2);
    var tl = Lab.el('<div><datalist id="' + dl + '">' + (ch.hero_templates || []).map(function (t) { return '<option value="' + Lab.esc(t) + '">'; }).join("") + "</datalist></div>");
    o.templates.forEach(function (t, i) {
      var inp = textInput(t, function (v) { o.templates[i] = v; V.saveDraft(); }, true);
      inp.setAttribute("list", dl);
      inp.style.marginBottom = "4px";
      tl.appendChild(inp);
    });
    fs.appendChild(field("templates", tl, "Unit templates this hero is (must exist for the civ)."));
    var strats = ch.stratagems || {};
    fs.appendChild(field("params.playbook", select(Object.keys(strats).map(function (k) { return [k, k + "  (" + strats[k].join(", ") + ")"]; }),
      o.params.playbook, function (v) { o.params.playbook = v; V.saveDraft(); }), "The stratagem he plays while he is in the field."));
    body.appendChild(fs);
    var tx = Lab.el("<fieldset><legend>Text: who he is, when to train him, how he fights</legend></fieldset>");
    o.text = o.text || [];
    tx.appendChild(linesEditor(o.text, V.saveDraft));
    body.appendChild(tx);
    var rest = {};
    Object.keys(o.params).forEach(function (k) { if (k !== "playbook") rest[k] = o.params[k]; });
    var rf = Lab.el("<fieldset><legend>Other params (JSON)</legend></fieldset>");
    rf.appendChild(jsonBox(rest, function (v) {
      var merged = {};
      if (o.params.playbook !== undefined) merged.playbook = o.params.playbook;
      Object.keys(v).forEach(function (k) { merged[k] = v[k]; });
      o.params = merged;
      V.saveDraft();
    }));
    body.appendChild(rf);
  };

  /* -- diff / apply ------------------------------------------------------------ */
  V.draftDiff = function () {
    var body = { path: V.path, a: "working", b: "draft" };
    if (V.mode === "raw") body.draft = V.text; else body.draft_data = V.obj;
    return Lab.post("/api/diff", body);
  };

  V.showDiff = function () {
    V.draftDiff().then(function (d) {
      Lab.dialog("Draft vs the file on disk", '<pre class="diff">' + Lab.diffHtml(d.diff) + "</pre>", "Close", { noOk: true, cancelText: "Close" });
    }).catch(function (e) { Lab.toast(e.message, true); });
  };

  V.applyFlow = function () {
    V.draftDiff().then(function (d) {
      var name = V.path.split("/").pop();
      var box = Lab.el('<div><div class="row"><label>Commit message</label><div class="grow"></div></div>' +
        '<p class="small muted">Commits only <code>' + Lab.esc(V.path) + '</code> on ' + Lab.esc((Lab.info || {}).required_branch || "strategos/mvp1") +
        ' as "Strategos lab: …". Nothing is pushed.</p><pre class="diff">' + Lab.diffHtml(d.diff) + "</pre></div>");
      var msg = textInput("edit " + name, function () { });
      box.querySelector(".grow").appendChild(msg);
      var dirty = null;
      if (V.doc.status) {
        dirty = Lab.el('<label class="note" style="display:block;margin:10px 0"><input type="checkbox"> This file already had uncommitted changes (' +
          Lab.esc(V.doc.status.trim()) + "): commit them together with this edit.</label>");
        box.insertBefore(dirty, box.firstChild);
      }
      if (Lab.info && Lab.info.live_note) box.insertBefore(Lab.el('<div class="note info" style="margin:0 0 10px">' + Lab.esc(Lab.info.live_note) + "</div>"), box.firstChild);
      (V.validation && V.validation.warnings || []).forEach(function (w) {
        box.appendChild(Lab.el('<div class="small" style="color:var(--warn)">! ' + Lab.esc(w) + "</div>"));
      });
      return Lab.dialog("Apply: write + commit " + name, box, "Apply & commit").then(function (ok) {
        if (!ok) return;
        var payload = V.payload();
        payload.base_sha256 = V.doc.sha256;
        return Lab.post("/api/apply", {
          changes: [payload], summary: msg.value, confirm_dirty: !!(dirty && dirty.querySelector("input").checked)
        }).then(function (res) {
          Lab.store.del(draftKey(V.path));
          Lab.toast("Committed " + res.short + ": " + res.message + (res.live_note ? "  (a game is running: see note)" : ""));
          V.afterWrite();
        });
      });
    }).catch(function (e) { Lab.toast(e.message, true); });
  };

  V.afterWrite = function () {
    Lab.refreshInfo();
    V.loadFiles().then(function () { V.open(V.path); });
    if (Lab.views.map) Lab.views.map.loaded = null;
  };

  /* -- history ------------------------------------------------------------------ */
  V.loadHistory = function () {
    var box = document.getElementById("edit-history");
    box.innerHTML = '<p class="muted small">Loading…</p>';
    Lab.get("/api/history", { path: V.path }).then(function (res) {
      V.history = res.log;
      if (!res.log.length) { box.innerHTML = '<p class="muted small">Not in git yet.</p>'; return; }
      box.innerHTML = "";
      res.log.forEach(function (c, i) {
        var prev = res.log[i + 1];
        var row = Lab.el('<div class="hist-row"><div class="s">' + Lab.esc(c.subject) + '</div><div class="meta"><code>' + Lab.esc(c.short) +
          "</code> · " + Lab.esc(Lab.fmtTime(c.date)) + " · " + Lab.esc(c.author) + '</div><div class="acts">' +
          '<button class="btn tiny js-view">View</button>' + (prev ? '<button class="btn tiny js-change">Its change</button>' : "") +
          '<button class="btn tiny js-now">Diff vs now</button>' + (i > 0 || (V.doc && V.doc.status) ? '<button class="btn tiny danger js-restore">Restore</button>' : "") + "</div></div>");
        row.querySelector(".js-view").addEventListener("click", function () {
          Lab.get("/api/show", { path: V.path, sha: c.sha }).then(function (s) {
            Lab.dialog(V.path.split("/").pop() + " @ " + c.short, '<pre class="block mono" style="max-height:60vh;overflow:auto">' + Lab.esc(s.text || "(did not exist)") + "</pre>", "Close", { noOk: true, cancelText: "Close" });
          });
        });
        var ch = row.querySelector(".js-change");
        if (ch) ch.addEventListener("click", function () {
          Lab.get("/api/diff", { path: V.path, a: prev.sha, b: c.sha }).then(function (d) {
            Lab.dialog(c.short + ": " + c.subject, '<pre class="diff">' + Lab.diffHtml(d.diff) + "</pre>", "Close", { noOk: true, cancelText: "Close" });
          });
        });
        row.querySelector(".js-now").addEventListener("click", function () {
          Lab.get("/api/diff", { path: V.path, a: c.sha, b: "working" }).then(function (d) {
            Lab.dialog(c.short + " → the file now", '<pre class="diff">' + Lab.diffHtml(d.diff) + "</pre>", "Close", { noOk: true, cancelText: "Close" });
          });
        });
        var rs = row.querySelector(".js-restore");
        if (rs) rs.addEventListener("click", function () { V.restoreFlow(c); });
        box.appendChild(row);
      });
    }).catch(function (e) { box.innerHTML = '<p class="pill err">' + Lab.esc(e.message) + "</p>"; });
  };

  V.restoreFlow = function (c) {
    Lab.get("/api/diff", { path: V.path, a: "working", b: c.sha }).then(function (d) {
      var box = Lab.el('<div><p>Write <code>' + Lab.esc(V.path.split("/").pop()) + "</code> as it was at <code>" + Lab.esc(c.short) +
        "</code> and commit that as a new commit (no checkout, nothing else touched).</p>" +
        (V.isDraft() ? '<p class="note">Your unsaved draft of this file will be discarded.</p>' : "") +
        '<pre class="diff">' + Lab.diffHtml(d.diff) + "</pre></div>");
      var dirty = null;
      if (V.doc.status) {
        dirty = Lab.el('<label class="note" style="display:block;margin:10px 0"><input type="checkbox"> The file has uncommitted changes: overwrite and commit.</label>');
        box.insertBefore(dirty, box.firstChild);
      }
      return Lab.dialog("Restore " + c.short, box, "Restore & commit", { danger: true }).then(function (ok) {
        if (!ok) return;
        return Lab.post("/api/restore", { path: V.path, sha: c.sha, confirm_dirty: !!(dirty && dirty.querySelector("input").checked) }).then(function (res) {
          Lab.store.del(draftKey(V.path));
          Lab.toast("Committed " + res.short + ": " + res.message);
          V.afterWrite();
        });
      });
    }).catch(function (e) { Lab.toast(e.message, true); });
  };

  /* -- L8: + clone a civ ------------------------------------------------------- */
  Lab.cloneFlow = function (src) {
    Lab.get("/api/civcodes").then(function (cc) {
      var free = cc.codes.filter(function (c) { return !c.has_civ_file; });
      var box = Lab.el('<div><p class="small muted">Copies <code>civs/' + Lab.esc(src) + '.json</code> and its hero files to a new civ, then says what cannot work there. Nothing is written until you press Clone.</p>' +
        '<div class="row"><label>From</label><div class="grow"></div></div>' +
        '<div class="row"><label>New civ code</label><div class="grow"></div></div>' +
        '<div class="row"><label>Name</label><div class="grow"></div></div>' +
        '<p><button class="btn js-prev">Preview</button></p><div class="js-out"></div></div>');
      var grows = box.querySelectorAll(".grow");
      var from = select(cc.civ_files, src, function () { });
      grows[0].appendChild(from);
      var to = select(free.map(function (c) { return [c.code, c.code + " (" + c.culture + ")"]; }), free.length ? free[0].code : "", function () { out.innerHTML = ""; });
      grows[1].appendChild(to);
      var name = textInput("", function () { });
      name.placeholder = "e.g. Rome";
      grows[2].appendChild(name);
      var out = box.querySelector(".js-out");
      function preview() {
        return Lab.post("/api/clone/preview", { src: from.value, dst: to.value, name: name.value || null }).then(function (pv) {
          if (!pv.ok) { out.innerHTML = '<p class="pill err">' + Lab.esc(pv.error) + "</p>"; return pv; }
          var lv = { "cannot work": "cannot", text: "text", check: "check", note: "note" };
          out.innerHTML = "<p><b>Files</b> (" + pv.files.length + "):</p><ul class=\"small\">" +
            pv.files.map(function (f) { return "<li><code>" + Lab.esc(f.path) + "</code></li>"; }).join("") + "</ul>" +
            "<p><b>What cannot work there yet</b> (" + pv.issues.length + "):</p><ul class=\"issues small\">" +
            pv.issues.map(function (i) {
              return '<li><span class="lvl ' + (lv[i.level] || "note") + '">' + Lab.esc(i.level) + "</span>" + Lab.esc(i.what) +
                (i.detail ? ' <span class="muted">— ' + Lab.esc(i.detail) + "</span>" : "") + "</li>";
            }).join("") + "</ul>" +
            (pv.hero_templates && pv.hero_templates.length ? '<p class="small muted">' + Lab.esc(to.value) + "'s hero templates: " + pv.hero_templates.map(Lab.esc).join(", ") + "</p>" : "");
          return pv;
        });
      }
      box.querySelector(".js-prev").addEventListener("click", function (e) { e.preventDefault(); preview(); });
      if (!free.length) out.innerHTML = '<p class="muted">Every playable civ already has a civ file.</p>';
      else preview();
      return Lab.dialog("New civ from " + src, box, "Clone & commit").then(function (ok) {
        if (!ok || !to.value) return;
        return Lab.post("/api/clone", { src: from.value, dst: to.value, name: name.value || null }).then(function (res) {
          Lab.toast("Committed " + res.short + ": " + res.message);
          Lab.refreshInfo();
          if (Lab.views.map) Lab.views.map.loaded = null;
          V.loadFiles();
          Lab.show("map", { civ: to.value });
        });
      });
    }).catch(function (e) { Lab.toast(e.message, true); });
  };
})();
