// Aktualisiert Fortschrittsbalken (Liste und Detailseite) ohne die Seite neu zu laden.
(function () {
  function zeichnen(el, d) {
    var balken = el.querySelector(".pv-balken > div");
    if (balken) balken.style.width = d.fortschritt + "%";
    var text = el.querySelector(".pv-text");
    if (text) text.textContent = d.status_text + (d.schritt ? " - " + d.schritt : "") + " (" + d.fortschritt + " %)";
  }
  function pruefen() {
    var elemente = document.querySelectorAll(".pv-fortschritt[data-url]");
    var laufend = false;
    elemente.forEach(function (el) {
      if (el.dataset.fertig === "1") return;
      laufend = true;
      fetch(el.dataset.url, {credentials: "same-origin"}).then(function (r) { return r.json(); }).then(function (d) {
        zeichnen(el, d);
        if (d.status !== "wird_bereitgestellt") { el.dataset.fertig = "1"; location.reload(); }
      }).catch(function () {});
    });
    if (laufend) setTimeout(pruefen, 3000);
  }
  // Start/Stopp/Neustart-Knoepfe in der Instanzliste: schicken ein POST mit dem CSRF-Token aus dem Cookie.
  function cookie(name) {
    var treffer = document.cookie.match("(?:^|; )" + name + "=([^;]*)");
    return treffer ? decodeURIComponent(treffer[1]) : "";
  }
  document.addEventListener("click", function (e) {
    var knopf = e.target.closest(".pv-knopf");
    if (!knopf || knopf.disabled) return;
    if (knopf.dataset.frage && !window.confirm(knopf.dataset.frage)) return;
    var form = document.createElement("form");
    form.method = "post";
    form.action = knopf.dataset.url;
    var feld = document.createElement("input");
    feld.type = "hidden";
    feld.name = "csrfmiddlewaretoken";
    feld.value = cookie("csrftoken");
    form.appendChild(feld);
    document.body.appendChild(form);
    form.submit();
  });
  document.addEventListener("DOMContentLoaded", pruefen);
})();
