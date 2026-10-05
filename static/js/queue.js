/* The Queue view: every plan that entered Petra's queues in a run -- when, by whom
   (Petra, or our persona -> leaf: option), and what the King did about it.  Live: the
   table is fetched again every 3 s while the run's game is running. */
"use strict";

(function () {
  var V = Lab.views.queue = { runs: [], run: null, data: null, timer: null, busy: false };

  function mmss(s) {
    if (s === null || s === undefined) return "";
    s = Math.max(0, Math.floor(s));
    return Math.floor(s / 60) + ":" + (s % 60 < 10 ? "0" : "") + (s % 60);
  }

  /* "q#N", a link to that question in the Game view. */
  function qlink(qid, idx) {
    if (qid === null || qid === undefined) return "";
    if (idx === null || idx === undefined) return "q#" + Lab.esc(qid);
    return '<a class="q" data-idx="' + idx + '" data-qid="' + qid + '">q#' + Lab.esc(qid) + "</a>";
  }

  V.init = function () {
    document.getElementById("queue-run").addEventListener("change", function (e) { V.open(e.target.value); });
    document.getElementById("queue-filter").addEventListener("change", V.render);
    document.getElementById("queue-ours").addEventListener("change", V.render);
    document.getElementById("queue-rows").addEventListener("click", function (e) {
      var a = e.target.closest("a.q");
      if (a) Lab.views.game.openQuestion(V.run, +a.dataset.idx);
    });
  };

  V.shown = function () {
    V.loadRuns().then(function () {
      if (!V.run) {
        var saved = Lab.store.get("lab.queue.run", null);
        var pick = V.runs.filter(function (r) { return r.live; })[0] ||
          V.runs.filter(function (r) { return r.id === saved; })[0] || V.runs[0];
        if (pick) V.open(pick.id);
      } else {
        V.fetch();
      }
    });
    if (!V.timer) {
      V.timer = setInterval(function () {
        if (Lab.state.tab === "queue" && V.data && V.data.info && V.data.info.live) V.fetch();
      }, 3000);
    }
  };

  V.loadRuns = function () {
    return Lab.get("/api/runs").then(function (res) {
      V.runs = res.runs.slice().sort(function (a, b) { return b.mtime - a.mtime; });
      document.getElementById("queue-run").innerHTML = V.runs.map(function (r) {
        var d = new Date(r.mtime * 1000), pad = function (n) { return (n < 10 ? "0" : "") + n; };
        var when = pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
        var who = (r.seats || []).map(function (s) { return s.label; }).join(" vs ");
        return '<option value="' + Lab.esc(r.id) + '"' + (r.id === V.run ? " selected" : "") + ">" +
          Lab.esc(when + "  ·  " + r.id.replace(/^results\//, "") + (who ? "  ·  " + who : "") + (r.live ? "  ·  LIVE" : "")) + "</option>";
      }).join("");
    }).catch(function (e) { Lab.toast("Runs: " + e.message, true); });
  };

  V.open = function (rid) {
    V.run = rid;
    V.data = null;
    Lab.store.set("lab.queue.run", rid);
    document.getElementById("queue-run").value = rid;
    document.getElementById("queue-rows").innerHTML = "";
    V.fetch();
  };

  V.fetch = function () {
    if (V.busy || !V.run) return;
    V.busy = true;
    var rid = V.run;
    document.getElementById("queue-status").textContent = "loading…";
    Lab.get("/api/queue", { run: rid }).then(function (res) {
      if (rid !== V.run) return;
      V.data = res;
      V.fillQueues(res);
      V.render();
      document.getElementById("queue-status").textContent =
        "p" + res.player + " · up to " + mmss(res.end) + (res.info && res.info.live ? " · LIVE, every 3 s" : "");
    }).catch(function (e) {
      document.getElementById("queue-status").textContent = "";
      Lab.toast("Queue: " + e.message, true);
    }).then(function () { V.busy = false; });
  };

  V.fillQueues = function (res) {
    var sel = document.getElementById("queue-filter"), keep = sel.value;
    var seen = {};
    res.rows.forEach(function (r) { seen[r.queue] = r.title; });
    sel.innerHTML = '<option value="">all</option>' + Object.keys(seen).sort().map(function (q) {
      return '<option value="' + Lab.esc(q) + '">' + Lab.esc(seen[q]) + "</option>";
    }).join("");
    sel.value = seen[keep] !== undefined ? keep : "";
  };

  V.render = function () {
    var res = V.data;
    if (!res) return;
    var only = document.getElementById("queue-filter").value;
    var ours = document.getElementById("queue-ours").checked;
    var s = res.summary;
    var answers = Object.keys(s.king_answers).map(function (k) { return k + " " + s.king_answers[k]; }).join(", ");
    document.getElementById("queue-summary").innerHTML =
      '<span class="pill">' + s.plans + " plans</span>" +
      '<span class="pill acc">' + s.ours + " ours (X)</span>" +
      '<span class="pill">' + s.king_asks + " King asks" + (answers ? ": " + Lab.esc(answers) : "") + "</span>" +
      '<span class="pill ' + (s.king_moves ? "ok" : "warn") + '">' + s.king_moves + " King moves (K)</span>" +
      (s.not_seen.length ? '<span class="pill err">sent, never seen in a queue: q#' + s.not_seen.join(", q#") + "</span>" : "");
    var html = [];
    res.rows.forEach(function (r, i) {
      if (only && r.queue !== only) return;
      if (ours && !r.by) return;
      var by = "Petra";
      if (r.by) {
        by = r.by.persona ? Lab.esc(r.by.persona) + " → " + Lab.esc(r.by.leaf) + ": " + Lab.esc(r.by.option) + " (" + qlink(r.by.qid, r.by.idx) + ")"
          : qlink(r.by.qid, r.by.idx);
      }
      var king = "";
      if (r.king) king = r.king.not_asked ? '<span class="muted">not asked (only keep possible)</span>'
        : qlink(r.king.qid, r.king.idx) + " " + Lab.esc(r.king.choice || "no answer");
      else if (r.by) king = '<span class="muted">—</span>';
      var k = r.k ? '<span title="' + Lab.esc(r.k.action) + '">K</span> ' + qlink(r.k.qid, r.k.idx) : "";
      html.push('<tr class="' + (r.by ? "ours " : "") + r.status + '">' +
        '<td class="n">' + (i + 1) + "</td><td>" + mmss(r.in) + "</td><td>" + Lab.esc(r.title) + "</td>" +
        "<td>" + Lab.esc(r.item) + (r.n > 1 ? " ×" + r.n : "") + "</td>" +
        '<td class="c">' + (r.by ? "X" : "") + "</td><td>" + by + "</td><td>" + king + "</td>" +
        '<td class="c">' + k + '</td><td class="out">' + (r.out !== null ? mmss(r.out) + " " : "") + Lab.esc(r.status) + "</td></tr>");
    });
    document.getElementById("queue-rows").innerHTML = html.join("") ||
      '<tr><td colspan="9" class="muted">No plans in this run' + (only || ours ? " for this filter" : "") +
      " (runs before the strategos AI wrote queue lines have none).</td></tr>";
  };
})();
