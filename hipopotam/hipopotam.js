// Ruchy hipopotama: kliknięcie = podskok; window.podskocz(el) i window.zapros(el) z kodu.
// Nowy ruch w trakcie trwającego jest ignorowany: hipopotam najpierw kończy poprzedni.
// Koniec liczony z animacji klatki, bo ona działa też przy ograniczonym ruchu.
(function () {
  var KONIEC = {
    "hipopotam-klatka-skok": "podskok",
    "hipopotam-klatka-zaprasza": "zaprasza",
  };
  function rusz(el, ruch) {
    if (!el.dataset.ruch) el.dataset.ruch = ruch;
  }
  function ozyw(el) {
    el.addEventListener("click", function () { rusz(el, "podskok"); });
    el.addEventListener("animationend", function (e) {
      if (KONIEC[e.animationName] === el.dataset.ruch) delete el.dataset.ruch;
    });
  }
  window.podskocz = function (el) { rusz(el, "podskok"); };
  window.zapros = function (el) { rusz(el, "zaprasza"); };
  document.querySelectorAll(".hipopotam").forEach(ozyw);
})();
