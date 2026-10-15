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

// "Mudar de grupo" (tela Postar): o ⇄ de cada post abre a janela com os grupos; o grupo atual
// fica de fora. Sem este arquivo, o ⇄ leva à página do post, que tem o mesmo formulário.
(function () {
  "use strict";

  var dialog = document.getElementById("mudar-grupo");
  if (!dialog || typeof dialog.showModal !== "function") {
    return;
  }
  var form = dialog.querySelector("form");
  var select = form.querySelector("select[name=niche_id]");
  var product = dialog.querySelector("[data-produto]");

  document.querySelectorAll("[data-mudar-grupo]").forEach(function (link) {
    link.addEventListener("click", function (ev) {
      ev.preventDefault();
      form.action = "/postar/" + link.getAttribute("data-pub") + "/grupo";
      product.textContent = link.getAttribute("data-nome");
      var current = link.getAttribute("data-nicho");
      var first = null;
      Array.prototype.forEach.call(select.options, function (opt) {
        var same = opt.value === current;
        opt.disabled = same;
        opt.hidden = same;
        if (!same && first === null) {
          first = opt;
        }
      });
      if (first) {
        select.value = first.value;
      }
      dialog.showModal();
      select.focus();
    });
  });

  dialog.querySelector("[data-fechar]").addEventListener("click", function () {
    dialog.close();
  });
  dialog.addEventListener("click", function (ev) {
    if (ev.target === dialog) {
      dialog.close(); // clique fora da caixa
    }
  });
})();

// "Enviar também em" (tela Postar): o ＋ abre a janela com caixinhas dos outros grupos. Sem
// este arquivo, o ＋ leva à página do post, que tem o mesmo formulário.
(function () {
  "use strict";

  var dialog = document.getElementById("enviar-tambem");
  if (!dialog || typeof dialog.showModal !== "function") {
    return;
  }
  var form = dialog.querySelector("form");
  var product = dialog.querySelector("[data-produto]");
  var error = dialog.querySelector("[data-erro]");
  var labels = Array.prototype.slice.call(dialog.querySelectorAll("label[data-canal]"));

  document.querySelectorAll("[data-enviar-tambem]").forEach(function (link) {
    link.addEventListener("click", function (ev) {
      ev.preventDefault();
      form.action = "/postar/" + link.getAttribute("data-pub") + "/copiar";
      product.textContent = link.getAttribute("data-nome");
      var current = link.getAttribute("data-canal");
      labels.forEach(function (label) {
        var box = label.querySelector("input");
        var same = label.getAttribute("data-canal") === current;
        label.hidden = same;
        box.disabled = same;
        box.checked = false;
      });
      error.hidden = true;
      dialog.showModal();
    });
  });

  form.addEventListener("change", function () {
    error.hidden = true;
  });
  form.addEventListener("submit", function (ev) {
    if (!form.querySelector("input[name=channel_id]:checked")) {
      ev.preventDefault();
      error.hidden = false;
    }
  });
  dialog.querySelector("[data-fechar]").addEventListener("click", function () {
    dialog.close();
  });
  dialog.addEventListener("click", function (ev) {
    if (ev.target === dialog) {
      dialog.close();
    }
  });
})();

// "Grupos ▾" do Aprovar (fila e produto): o resumo acompanha as caixinhas, aprovar sem nenhum
// grupo marcado pede um, e a lista fecha ao clicar fora ou com Esc. Sem este arquivo, as
// caixinhas funcionam igual e o servidor recusa a aprovação sem grupo.
(function () {
  "use strict";

  var drops = Array.prototype.slice.call(document.querySelectorAll("details[data-grupos]"));
  if (!drops.length) {
    return;
  }

  drops.forEach(function (drop) {
    var formId = drop.getAttribute("data-grupos");
    var form = document.getElementById(formId);
    var summary = drop.querySelector("[data-resumo]");
    var error = drop.querySelector("[data-erro]");
    var boxes = Array.prototype.slice.call(drop.querySelectorAll("input[name=channel_id]"));

    function checked() {
      return boxes.filter(function (b) { return b.checked; });
    }

    drop.addEventListener("change", function () {
      var names = checked().map(function (b) { return b.parentNode.textContent.trim(); });
      summary.textContent = names.length ? names.join(" + ") : "nenhum grupo";
      error.hidden = names.length > 0;
    });
    drop.addEventListener("toggle", function () {
      if (drop.open) {
        drops.forEach(function (other) {
          if (other !== drop) {
            other.open = false;
          }
        });
      }
    });
    if (form && form.querySelector("input[name=grupos]")) {
      form.addEventListener("submit", function (ev) {
        if (!checked().length) {
          ev.preventDefault();
          drop.open = true;
          error.hidden = false;
        }
      });
    }
  });

  document.addEventListener("click", function (ev) {
    drops.forEach(function (drop) {
      if (drop.open && !drop.contains(ev.target)) {
        drop.open = false;
      }
    });
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") {
      drops.forEach(function (drop) { drop.open = false; });
    }
  });
})();

// Fila de aprovação: decidir sem recarregar (antes cada aprovação jogava para o topo da página)
// e aprovar vários de uma vez. Cada card tem ✓ (aprovar) e ✕ (rejeitar); a barra que acompanha a
// rolagem aplica os marcados um por vez, na ordem da tela (que vira a ordem da fila de envio),
// pelo mesmo /decisao do botão do card, com o Pix, o cupom e os grupos do próprio card. Problema
// num card não para os outros: ele fica marcado com a mensagem. As marcas sobrevivem a recarregar
// a aba (6 h). Sem este arquivo, os botões de cada card funcionam e o servidor volta para o card
// seguinte.
(function () {
  "use strict";

  var cards = Array.prototype.slice.call(document.querySelectorAll("article.card[data-card]"));
  var bar = document.querySelector("[data-lote]");
  if (!cards.length || !bar || !window.fetch || !window.FormData || !window.Promise) {
    return;
  }
  document.documentElement.classList.add("js");

  var KEY = "fila-marcas";
  var KEEP_MS = 6 * 60 * 60 * 1000;
  var TIMEOUT_MS = 20000;
  var FIELDS = ["pix", "cupom", "ultimas", "acaba_em"];
  var title = bar.querySelector("[data-lote-titulo]");
  var hint = bar.querySelector("[data-lote-dica]");
  var apply = bar.querySelector("[data-lote-aplicar]");
  var clear = bar.querySelector("[data-lote-limpar]");
  var progress = bar.querySelector("[data-lote-progresso]");
  var toasts = document.querySelector("[data-toasts]");
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var running = false;
  var current = null;
  var refreshTimer = null;

  function live() {
    // fora os que já foram decididos e estão saindo (a animação ainda os deixa na página)
    return cards.filter(function (c) { return c.isConnected && !c.hasAttribute("data-saindo"); });
  }

  function cardOf(el) {
    return el && el.closest ? el.closest("article.card[data-card]") : null;
  }

  function nameOf(card) {
    var link = card.querySelector(".nome a");
    var text = link ? link.textContent.trim() : "Produto";
    return text.length > 48 ? text.slice(0, 47) + "…" : text;
  }

  // ------------------------------------------------------------------ marcas

  function loadMarks() {
    try {
      var raw = JSON.parse(window.sessionStorage.getItem(KEY) || "{}") || {};
      var now = Date.now();
      Object.keys(raw).forEach(function (id) {
        if (!raw[id] || now - raw[id][1] > KEEP_MS) {
          delete raw[id];
        }
      });
      return raw;
    } catch (e) {
      return {};
    }
  }

  var marks = loadMarks();

  function saveMarks() {
    try {
      window.sessionStorage.setItem(KEY, JSON.stringify(marks));
    } catch (e) {
      /* sem armazenamento: as marcas valem só enquanto a página está aberta */
    }
  }

  function setMark(card, value) {
    var id = card.getAttribute("data-card");
    if (value) {
      card.setAttribute("data-marca", value);
      marks[id] = [value, Date.now()];
    } else {
      card.removeAttribute("data-marca");
      delete marks[id];
    }
    Array.prototype.forEach.call(card.querySelectorAll(".marcar button"), function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-marca") === value));
    });
    showMsg(card, "");
    saveMarks();
    render();
  }

  function toggle(card, value) {
    setMark(card, card.getAttribute("data-marca") === value ? null : value);
  }

  // ------------------------------------------------------------------ barra e avisos

  function plural(n, one, many) {
    return n + " " + (n === 1 ? one : many);
  }

  function render() {
    if (running) {
      return;
    }
    var approve = 0;
    var reject = 0;
    live().forEach(function (c) {
      var m = c.getAttribute("data-marca");
      if (m === "approved") {
        approve++;
      } else if (m === "rejected") {
        reject++;
      }
    });
    var total = approve + reject;
    apply.disabled = total === 0;
    clear.hidden = total === 0;
    hint.hidden = total > 0;
    bar.classList.toggle("marcado", total > 0);
    if (!total) {
      title.textContent = "Nada marcado";
      apply.textContent = "Aprovar";
      return;
    }
    var parts = [];
    if (approve) {
      parts.push(approve + " para aprovar");
    }
    if (reject) {
      parts.push(reject + " para rejeitar");
    }
    title.textContent = parts.join(" · ");
    if (!reject) {
      apply.textContent = "Aprovar " + approve;
    } else if (!approve) {
      apply.textContent = "Rejeitar " + reject;
    } else {
      apply.textContent = "Aplicar " + total;
    }
  }

  function toast(text, kind) {
    if (!toasts) {
      return;
    }
    var p = document.createElement("p");
    p.className = kind;
    p.textContent = text;
    toasts.appendChild(p);
    while (toasts.children.length > 3) {
      toasts.removeChild(toasts.firstChild);
    }
    window.setTimeout(function () { p.remove(); }, kind === "falha" ? 9000 : 4500);
  }

  function showMsg(card, text) {
    var box = card.querySelector("[data-msg]");
    if (box) {
      box.textContent = text;
    }
  }

  // ------------------------------------------------------------------ envio

  function send(form) {
    var controller = window.AbortController ? new window.AbortController() : null;
    var timer = controller ? window.setTimeout(function () { controller.abort(); }, TIMEOUT_MS) : null;
    return window.fetch(form.action, {
      method: "POST",
      body: new window.FormData(form),
      headers: { Accept: "application/json" },
      credentials: "same-origin",
      signal: controller ? controller.signal : undefined
    }).then(function (res) {
      var type = res.headers.get("content-type") || "";
      if (type.indexOf("application/json") === -1) {
        // a sessão venceu: o servidor mandou para a tela de login
        return {
          ok: false,
          stop: true,
          relogin: true,
          mensagem: "A sessão venceu: entre de novo (as marcas ficam guardadas)."
        };
      }
      return res.json().then(function (data) {
        var detail = typeof data.detail === "string" ? data.detail : null;
        var relogin = res.status === 401 || res.status === 403;
        return {
          ok: res.ok && data.ok !== false,
          stop: relogin,
          relogin: relogin,
          mensagem: data.mensagem || detail || "Não deu certo (erro " + res.status + ")."
        };
      });
    }, function () {
      return {
        ok: false,
        stop: true,
        mensagem: "Sem resposta do servidor. Confira a conexão e tente de novo."
      };
    }).then(function (result) {
      if (timer) {
        window.clearTimeout(timer);
      }
      return result;
    });
  }

  function removeCard(card) {
    delete marks[card.getAttribute("data-card")];
    saveMarks();
    card.setAttribute("data-saindo", "");
    if (current === card) {
      current = null;
    }
    var finished = false;
    function finish() {
      if (finished) {
        return;
      }
      finished = true;
      card.remove();
      afterRemove();
    }
    if (reduce) {
      finish();
      return;
    }
    card.style.height = card.offsetHeight + "px";
    void card.offsetHeight; // fixa a altura antes da transição
    card.classList.add("saindo");
    card.style.height = "0px";
    card.addEventListener("transitionend", finish);
    window.setTimeout(finish, 450);
  }

  function afterRemove() {
    render();
    scheduleRefresh();
    if (live().length || !bar.isConnected) {
      return;
    }
    var empty = document.createElement("p");
    empty.className = "vazio";
    empty.appendChild(document.createTextNode("Página limpa. "));
    var more = document.createElement("a");
    more.href = window.location.pathname + window.location.search;
    more.textContent = "Carregar os próximos";
    empty.appendChild(more);
    bar.parentNode.insertBefore(empty, bar);
    bar.remove();
  }

  function decide(card, form, quiet) {
    card.classList.add("ocupado");
    showMsg(card, "");
    return send(form).then(function (r) {
      card.classList.remove("ocupado");
      if (r.ok) {
        if (!quiet) {
          toast(nameOf(card) + ": " + r.mensagem, "ok");
        }
        removeCard(card);
      } else {
        showMsg(card, r.mensagem);
        if (!quiet) {
          toast(nameOf(card) + ": " + r.mensagem, "falha");
        }
      }
      if (r.relogin) {
        window.setTimeout(function () { window.location.reload(); }, 2500);
      }
      return r;
    });
  }

  function groupsOk(card, form) {
    if (!form.querySelector("input[name=grupos]")) {
      return true;
    }
    var any = Array.prototype.some.call(form.elements, function (el) {
      return el.name === "channel_id" && el.checked;
    });
    if (!any) {
      showMsg(card, "Marque pelo menos um grupo em “Grupos ▾”.");
    }
    return any;
  }

  function applyMarked() {
    if (running) {
      return;
    }
    var queue = live().filter(function (c) { return c.getAttribute("data-marca"); });
    if (!queue.length) {
      return;
    }
    running = true;
    bar.setAttribute("aria-busy", "true");
    apply.disabled = true;
    clear.hidden = true;
    var done = { approved: 0, rejected: 0 };
    var failed = 0;
    var i = 0;

    function finish(stopMessage) {
      running = false;
      bar.removeAttribute("aria-busy");
      progress.style.width = "0";
      var parts = [];
      if (done.approved) {
        parts.push(plural(done.approved, "aprovado", "aprovados"));
      }
      if (done.rejected) {
        parts.push(plural(done.rejected, "rejeitado", "rejeitados"));
      }
      if (parts.length) {
        toast(parts.join(" · ") + ".", "ok");
      }
      if (stopMessage) {
        toast(stopMessage, "falha");
      } else if (failed) {
        toast(
          plural(failed, "ficou marcado", "ficaram marcados") + " com problema: veja a mensagem no card.",
          "falha"
        );
      }
      render();
      scheduleRefresh();
      var left = live().filter(function (c) { return c.getAttribute("data-marca"); });
      if (failed && left.length) {
        focusCard(left[0], true);
      }
    }

    function step() {
      if (i >= queue.length) {
        finish(null);
        return null;
      }
      var card = queue[i++];
      title.textContent = "Aplicando " + i + " de " + queue.length + "…";
      progress.style.width = Math.round(((i - 1) / queue.length) * 100) + "%";
      var value = card.getAttribute("data-marca");
      var form = value && card.isConnected
        ? card.querySelector('form[data-decisao="' + value + '"]')
        : null;
      if (!form) {
        return step(); // desmarcado ou decidido no meio do caminho
      }
      if (value === "approved" && !groupsOk(card, form)) {
        failed++;
        return step();
      }
      return decide(card, form, true).then(function (r) {
        if (r.ok) {
          done[value]++;
        } else {
          failed++;
        }
        if (r.stop) {
          finish(r.mensagem);
          return null;
        }
        return step();
      });
    }

    step();
  }

  function scheduleRefresh() {
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(refreshSide, 900);
  }

  // Contagens do topo e "Prontos pra postar" vêm da página de novo, sem mexer nos cards.
  function refreshSide() {
    if (running) {
      scheduleRefresh();
      return;
    }
    window.fetch(window.location.href, { headers: { Accept: "text/html" }, credentials: "same-origin" })
      .then(function (res) { return res.ok ? res.text() : null; })
      .then(function (html) {
        if (!html || !window.DOMParser) {
          return;
        }
        var doc = new window.DOMParser().parseFromString(html, "text/html");
        [".prontos", ".contagem"].forEach(function (sel) {
          var fresh = doc.querySelector(sel);
          var old = document.querySelector(sel);
          if (fresh && old) {
            old.replaceWith(document.importNode(fresh, true));
          }
        });
      })
      .catch(function () { /* fica a contagem anterior */ });
  }

  // ------------------------------------------------------------------ teclado e foco

  function focusCard(card, scroll) {
    if (current) {
      current.classList.remove("atual");
    }
    current = card;
    if (!card) {
      return;
    }
    card.classList.add("atual");
    if (scroll) {
      card.focus({ preventScroll: true });
      card.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
    }
  }

  function move(step) {
    var all = live();
    if (!all.length) {
      return;
    }
    if (!current || !current.isConnected) {
      for (var i = 0; i < all.length; i++) {
        if (all[i].getBoundingClientRect().bottom > 90) {
          focusCard(all[i], true);
          return;
        }
      }
      focusCard(all[all.length - 1], true);
      return;
    }
    var next = all.indexOf(current) + step;
    focusCard(all[Math.max(0, Math.min(all.length - 1, next))], true);
  }

  function typing(el) {
    return el && (el.isContentEditable || /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName));
  }

  // ------------------------------------------------------------------ eventos

  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest ? ev.target.closest(".marcar button[data-marca]") : null;
    if (!btn) {
      return;
    }
    var card = cardOf(btn);
    if (card && !card.classList.contains("ocupado")) {
      toggle(card, btn.getAttribute("data-marca"));
      focusCard(card, false);
    }
  });

  // Botões do card (Aprovar, Rejeitar, Bloquear) sem recarregar a página. A checagem de grupos
  // do "Grupos ▾" roda antes, no próprio formulário, e pode cancelar o envio.
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (ev.defaultPrevented || !form.matches || !form.matches("form[data-decisao]")) {
      return;
    }
    var card = cardOf(form);
    if (!card) {
      return;
    }
    ev.preventDefault();
    if (!running && !card.classList.contains("ocupado")) {
      focusCard(card, false);
      decide(card, form, false);
    }
  });

  // Preencher Pix, cupom, unidades ou relâmpago é intenção de aprovar: marca ✓ sozinho.
  document.addEventListener("input", function (ev) {
    var el = ev.target;
    if (!el.form || FIELDS.indexOf(el.name) === -1 || !el.value.trim()) {
      return;
    }
    var card = cardOf(el);
    if (card && !card.getAttribute("data-marca")) {
      setMark(card, "approved");
    }
  });

  document.addEventListener("focusin", function (ev) {
    var card = cardOf(ev.target);
    if (card && card !== current) {
      focusCard(card, false);
    }
  });

  document.addEventListener("keydown", function (ev) {
    if (ev.ctrlKey && ev.key === "Enter") {
      ev.preventDefault();
      applyMarked();
      return;
    }
    if (ev.ctrlKey || ev.altKey || ev.metaKey || typing(ev.target)) {
      return;
    }
    var key = (ev.key || "").toLowerCase();
    if (key === "j" || key === "k") {
      ev.preventDefault();
      move(key === "j" ? 1 : -1);
    } else if ((key === "v" || key === "x") && current && current.isConnected) {
      ev.preventDefault();
      toggle(current, key === "v" ? "approved" : "rejected");
    }
  });

  apply.addEventListener("click", applyMarked);
  clear.addEventListener("click", function () {
    live().forEach(function (c) {
      if (c.getAttribute("data-marca")) {
        setMark(c, null);
      }
    });
  });
  window.addEventListener("beforeunload", function (ev) {
    if (running) {
      ev.preventDefault();
      ev.returnValue = ""; // aplicando: sair agora deixa parte sem decidir
    }
  });

  cards.forEach(function (card) {
    var mark = marks[card.getAttribute("data-card")];
    if (mark && (mark[0] === "approved" || mark[0] === "rejected")) {
      setMark(card, mark[0]);
    }
  });
  render();
})();
