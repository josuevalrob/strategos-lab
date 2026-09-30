/* Loads three.js (ES module, via the import map in index.html), three-spritetext and
   3d-force-graph, in that order.  3d-force-graph uses window.THREE when it is set when
   the bundle loads, so the page runs ONE three.js: the objects the lab adds (labels,
   lines, plane guides) and the library's own renderer share it. */
import * as THREE from "three";
import SpriteText from "three-spritetext";

window.THREE = THREE;
window.SpriteText = SpriteText;

export const ready = new Promise(function (resolve, reject) {
  if (window.ForceGraph3D) {
    resolve();
    return;
  }
  const s = document.createElement("script");
  s.src = "/static/vendor/3d-force-graph.min.js";
  s.onload = function () { resolve(); };
  s.onerror = function () { reject(new Error("could not load 3d-force-graph")); };
  document.head.appendChild(s);
});
