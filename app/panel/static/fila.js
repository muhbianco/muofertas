// Fila de envio (tela Postar): arrastar um post pela alça ⠿ para cima faz ele sair antes.
// Só sobe. Ao soltar, envia o mesmo formulário dos botões ▲/⤒ (que funcionam sem este arquivo).
(function () {
  "use strict";

  document.querySelectorAll("ol.fila-envio").forEach(function (list) {
    var form = list.parentNode.querySelector("form.mover");
    if (!form) {
      return;
    }
    var dragging = null;
    var startY = 0;
    var target = null;

    function rows() {
      return Array.prototype.slice.call(list.querySelectorAll("li[data-pub]"));
    }

    function clearTarget() {
      if (target) {
        target.classList.remove("alvo");
      }
      target = null;
    }

    list.addEventListener("pointerdown", function (ev) {
      var handle = ev.target.closest(".alca");
      if (!handle) {
        return;
      }
      dragging = handle.closest("li[data-pub]");
      startY = ev.clientY;
      dragging.classList.add("arrastando");
      handle.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    });

    list.addEventListener("pointermove", function (ev) {
      if (!dragging) {
        return;
      }
      // Só para cima: a linha acompanha o dedo, mas nunca desce.
      dragging.style.transform = "translateY(" + Math.min(ev.clientY - startY, 0) + "px)";
      clearTarget();
      var all = rows();
      var mine = all.indexOf(dragging);
      for (var i = 0; i < mine; i++) {
        if (ev.clientY < all[i].getBoundingClientRect().bottom) {
          target = all[i];
          target.classList.add("alvo");
          break;
        }
      }
    });

    function finish() {
      if (!dragging) {
        return;
      }
      var moved = dragging;
      var before = target;
      moved.classList.remove("arrastando");
      moved.style.transform = "";
      clearTarget();
      dragging = null;
      if (before) {
        form.action = "/postar/" + moved.getAttribute("data-pub") + "/subir";
        form.querySelector("input[name=antes_de]").value = before.getAttribute("data-pub");
        form.submit();
      }
    }

    list.addEventListener("pointerup", finish);
    list.addEventListener("pointercancel", finish);
  });
})();
