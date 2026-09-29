/* The belt network bar (beltholders.com/network-bar.js; the same file ships in all three site repos).
 * Fills every element marked data-belt-network with a link to every lineal belt and its current
 * holder, from https://beltholders.com/api/network.json. The build-time links inside the element
 * stay as they are if the fetch fails. data-site="cfb|cbb|bh" puts that site's own belts first;
 * after that come belts with a game in the next week, then the rest.
 */
(function () {
  var URL = "https://beltholders.com/api/network.json";
  var els = document.querySelectorAll("[data-belt-network]");
  if (!els.length || !window.fetch) return;
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  var OWN = { cfb: ["cfb"], cbb: ["cbb", "wcbb"], bh: [] };
  fetch(URL).then(function (r) { return r.json(); }).then(function (j) {
    var week = new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10);
    els.forEach(function (el) {
      var own = OWN[el.getAttribute("data-site")] || [];
      var here = location.hostname.replace(/^www\./, "");
      var bs = (j.belts || []).filter(function (b) { return b.ok && b.holder; }).slice();
      function rank(b) {
        if (own.indexOf(b.key) >= 0) return 0;
        if (b.next && b.next.date <= week) return 1;
        return b.state === "postseason_holder_out" ? 2 : 3;
      }
      bs.sort(function (a, b) {
        return rank(a) - rank(b) || ((a.next && a.next.date) || "9999").localeCompare((b.next && b.next.date) || "9999");
      });
      var links = bs.map(function (b) {
        var nx = b.next ? " · next " + b.next.date.slice(5).replace("-", "/") + (b.next.home || b.next.neutral ? " vs. " : " at ") + (b.next.opponent_short || b.next.opponent || "") : "";
        var url = b.url.indexOf(here) >= 0 ? b.url.replace(/^https?:\/\/[^/]+/, "") : b.url;
        return '<a href="' + esc(url) + '" title="' + esc(b.name + ": " + b.holder + nx) + '"><i style="background:' + esc(b.colors[0]) +
          '"></i><b>' + esc(b.short) + "</b> " + esc(b.holder_short || b.holder) + "</a>";
      });
      links.push('<a class="all" href="https://beltholders.com/all/">Every belt →</a>');
      var kc = el.getAttribute("data-kicker-class") || "nk";
      el.innerHTML = '<span class="' + esc(kc) + '">The belt network</span>' + links.join("");
      el.classList.add("live");
    });
  }).catch(function () {});
})();
