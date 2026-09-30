/* Strategos Lab: shared helpers, tabs, theme, dialog, toast. Plain script, no build. */
"use strict";

var Lab = window.Lab = {
  views: {},
  info: null,
  state: { tab: "map" }
};

Lab.esc = function (s) {
  return String(s === undefined || s === null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
};

Lab.el = function (html) {
  var t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
};

Lab.qs = function (params) {
  return Object.keys(params).filter(function (k) { return params[k] !== undefined && params[k] !== null && params[k] !== ""; })
    .map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(params[k]); }).join("&");
};

Lab.get = function (path, params) {
  var url = path + (params ? "?" + Lab.qs(params) : "");
  return fetch(url, { cache: "no-store" }).then(function (r) {
    return r.json().then(function (j) {
      if (!j.ok) { var e = new Error(j.error || ("HTTP " + r.status)); e.data = j; throw e; }
      return j;
    });
  });
};

Lab.post = function (path, body) {
  return fetch(path, {
    method: "POST", cache: "no-store",
    headers: { "Content-Type": "application/json", "X-Lab": "1" },
    body: JSON.stringify(body || {})
  }).then(function (r) {
    return r.json().then(function (j) {
      if (!j.ok) { var e = new Error(j.error || ("HTTP " + r.status)); e.data = j; throw e; }
      return j;
    });
  });
};

Lab.store = {
  get: function (k, dflt) {
    try { var v = localStorage.getItem(k); return v === null ? dflt : JSON.parse(v); } catch (e) { return dflt; }
  },
  set: function (k, v) {
    try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage off */ }
  },
  del: function (k) {
    try { localStorage.removeItem(k); } catch (e) { /* storage off */ }
  }
};

Lab.toast = function (msg, isErr) {
  var t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  clearTimeout(Lab._toastTimer);
  Lab._toastTimer = setTimeout(function () { t.className = "toast hidden"; }, isErr ? 7000 : 3500);
};

/* A modal: body is an element or html; resolves true on OK. */
Lab.dialog = function (title, body, okText, opts) {
  opts = opts || {};
  var dlg = document.getElementById("dlg");
  document.getElementById("dlg-title").textContent = title;
  var holder = document.getElementById("dlg-body");
  holder.innerHTML = "";
  if (typeof body === "string") holder.innerHTML = body; else if (body) holder.appendChild(body);
  var ok = document.getElementById("dlg-ok");
  ok.textContent = okText || "OK";
  ok.className = "btn " + (opts.danger ? "danger" : "primary");
  ok.classList.toggle("hidden", !!opts.noOk);
  document.getElementById("dlg-cancel").textContent = opts.cancelText || "Cancel";
  return new Promise(function (resolve) {
    dlg.onclose = function () { resolve(dlg.returnValue === "ok"); };
    dlg.returnValue = "";
    dlg.showModal();
  });
};

Lab.diffHtml = function (text) {
  if (!text) return '<span class="muted">(no difference)</span>';
  return text.split("\n").map(function (l) {
    var cls = l.indexOf("+") === 0 && l.indexOf("+++") !== 0 ? "add" :
      l.indexOf("-") === 0 && l.indexOf("---") !== 0 ? "del" : l.indexOf("@@") === 0 ? "hunk" : "";
    return cls ? '<span class="' + cls + '">' + Lab.esc(l) + "</span>" : Lab.esc(l) + "\n";
  }).join("");
};

Lab.fmtTime = function (iso) {
  try { var d = new Date(iso); return d.toLocaleString(); } catch (e) { return iso; }
};

Lab.css = function (name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
};

/* -- theme ---------------------------------------------------------------- */
Lab.theme = function () {
  return document.documentElement.getAttribute("data-theme") || "auto";
};
Lab.cycleTheme = function () {
  var order = ["auto", "light", "dark"];
  var next = order[(order.indexOf(Lab.theme()) + 1) % 3];
  if (next === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("lab.theme", next); } catch (e) { /* storage off */ }
  document.getElementById("theme-btn").textContent = "Theme: " + next;
  Lab.onTheme();
};
Lab.onTheme = function () {
  Object.keys(Lab.views).forEach(function (k) {
    if (Lab.views[k].onTheme) Lab.views[k].onTheme();
  });
};

/* -- tabs ------------------------------------------------------------------ */
Lab.show = function (tab, arg) {
  Lab.state.tab = tab;
  document.querySelectorAll(".tab").forEach(function (b) { b.classList.toggle("on", b.dataset.tab === tab); });
  document.querySelectorAll(".view").forEach(function (v) { v.classList.toggle("on", v.id === "view-" + tab); });
  Lab.store.set("lab.tab", tab);
  var view = Lab.views[tab];
  if (view && view.shown) view.shown(arg);
};

/* -- repo + live status ------------------------------------------------------- */
Lab.refreshInfo = function () {
  return Lab.get("/api/info").then(function (info) {
    Lab.info = info;
    var badge = document.getElementById("repo-badge");
    badge.textContent = info.branch + " @ " + info.head;
    badge.className = "badge" + (info.branch !== info.required_branch ? " bad" : "");
    badge.title = info.repo + (info.branch !== info.required_branch ?
      "\nApply is refused: the repo must be on " + info.required_branch : "");
    document.getElementById("live-badge").classList.toggle("hidden", !(info.live_runs || []).length);
    document.getElementById("live-badge").title = "Running: " + (info.live_runs || []).join(", ");
    var msgs = [];
    if (info.branch !== info.required_branch)
      msgs.push("The 0 A.D. repo is on " + info.branch + ": Apply / Restore / Clone are refused until it is on " + info.required_branch + ".");
    if ((info.code_errors || []).length)
      msgs.push("The map's code anchors no longer match head.js: " + info.code_errors.join("; "));
    var banner = document.getElementById("banner");
    banner.textContent = msgs.join("  ");
    banner.className = "banner" + (msgs.length ? " err" : " hidden");
    Object.keys(Lab.views).forEach(function (k) { if (Lab.views[k].onInfo) Lab.views[k].onInfo(info); });
    return info;
  }).catch(function (e) { Lab.toast("Server: " + e.message, true); });
};

Lab.start = function () {
  document.querySelectorAll(".tab").forEach(function (b) {
    b.addEventListener("click", function () { Lab.show(b.dataset.tab); });
  });
  document.getElementById("theme-btn").textContent = "Theme: " + Lab.theme();
  document.getElementById("theme-btn").addEventListener("click", Lab.cycleTheme);
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
      if (Lab.theme() === "auto") Lab.onTheme();
    });
  }
  Object.keys(Lab.views).forEach(function (k) { if (Lab.views[k].init) Lab.views[k].init(); });
  Lab.refreshInfo();
  setInterval(Lab.refreshInfo, 10000);
  Lab.show(Lab.store.get("lab.tab", "map"));
};
