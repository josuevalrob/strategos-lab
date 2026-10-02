/* Drag handles in the Game view: the log list's right border (left / right) and the
   top border of the In -> out panel (up = bigger).  Sizes are remembered per browser. */
"use strict";

(function () {
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
  function refit() {
    var V = Lab.views && Lab.views.game;
  }
  function drag(handle, onMove) {
    handle.addEventListener("pointerdown", function (ev) {
      ev.preventDefault();
      handle.setPointerCapture(ev.pointerId);
      handle.classList.add("drag");
      document.body.classList.add("dragging");
      function move(e) { onMove(e); }
      function up() {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        handle.classList.remove("drag");
        document.body.classList.remove("dragging");
        refit();
      }
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
    });
  }
  function init() {
    var left = document.querySelector("#view-game .timeline-pane"),
      mini = document.querySelector("#view-game .mini-wrap"),
      v = document.getElementById("game-vsplit"), h = document.getElementById("game-hsplit");
    if (!left || !mini || !v || !h) return;
    var w = +store("lab.game.left"), mh = +store("lab.game.mini");
    if (w) left.style.width = w + "px";
    if (mh) mini.style.flexBasis = mh + "px";
    drag(v, function (e) {
      var box = left.parentElement.getBoundingClientRect();
      var px = Math.max(260, Math.min(box.width - 300, e.clientX - box.left));
      left.style.width = px + "px";
      store("lab.game.left", Math.round(px));
    });
    drag(h, function (e) {
      var box = mini.parentElement.getBoundingClientRect();
      var px = Math.max(0, Math.min(box.height - 80, e.clientY - box.top));
      mini.style.flexBasis = px + "px";
      store("lab.game.mini", Math.round(px));
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
