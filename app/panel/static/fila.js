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

// "Enviar agora" manda o post para o grupo na hora: pede confirmação (um toque sem querer não
// publica nada). Sem este arquivo, o botão envia direto.
(function () {
  "use strict";

  document.querySelectorAll("form[data-confirmar]").forEach(function (form) {
    form.addEventListener("submit", function (ev) {
      if (!window.confirm(form.getAttribute("data-confirmar"))) {
        ev.preventDefault();
      }
    });
  });
})();

// Grupos recolhíveis (tela Postar): começam fechados; abre o grupo do endereço (#grupo-5, para
// onde as ações voltam) e os que estavam abertos neste aparelho. O estado guardado é só
// conveniência: sem ele (aba anônima, armazenamento bloqueado) a tela funciona igual.
(function () {
  "use strict";

  var KEY = "postar-abertos";
  var groups = Array.prototype.slice.call(document.querySelectorAll("details.grupo"));
  if (!groups.length) {
    return;
  }

  function load() {
    try {
      return JSON.parse(window.localStorage.getItem(KEY) || "[]");
    } catch (e) {
      return [];
    }
  }

  function save() {
    try {
      var open = groups.filter(function (g) { return g.open; }).map(function (g) { return g.id; });
      window.localStorage.setItem(KEY, JSON.stringify(open));
    } catch (e) {
      /* sem armazenamento: segue sem lembrar */
    }
  }

  var remembered = load();
  groups.forEach(function (g) {
    if (remembered.indexOf(g.id) !== -1 || "#" + g.id === window.location.hash) {
      g.open = true;
    }
    g.addEventListener("toggle", save);
  });
  if (window.location.hash) {
    var target = document.getElementById(window.location.hash.slice(1));
    if (target) {
      target.scrollIntoView();
    }
  }

  function setAll(open) {
    groups.forEach(function (g) { g.open = open; });
    save();
  }
  var openAll = document.querySelector("[data-abrir-todos]");
  var closeAll = document.querySelector("[data-fechar-todos]");
  if (openAll) {
    openAll.addEventListener("click", function () { setAll(true); });
  }
  if (closeAll) {
    closeAll.addEventListener("click", function () { setAll(false); });
  }
})();
