// dashboard/app.js  (FASE 11)
// Le data/dashboard_state.json e atualiza o painel da JARVIS.
// Sem framework, sem internet. Atualiza por polling leve (a cada 1s) e so
// re-renderiza quando o estado muda (nao trava o PC).

(() => {
  "use strict";

  // Caminho do estado. Servido localmente: /dashboard/ -> ../data/...
  const STATE_URL = "../data/dashboard_state.json";
  const POLL_MS = 1000;

  // Rotulos amigaveis por status (caixa alta, legivel de longe).
  const STATUS_LABEL = {
    aguardando:           "AGUARDANDO COMANDO",
    ocioso:               "AGUARDANDO COMANDO",
    ouvindo:              "OUVINDO",
    transcrevendo:        "TRANSCREVENDO",
    consultando:          "CONSULTANDO SISTEMA",
    pensando:             "PENSANDO",
    respondendo:          "RESPONDENDO",
    erro:                 "ERRO",
    dados_insuficientes:  "DADOS INSUFICIENTES",
  };

  // Cores aproximadas para a bolinha (swatch) dos materiais.
  const CORES = {
    vermelho: "#ff4d5e", preto: "#222", branco: "#eee",
    cinza: "#9aa7b0", azul: "#3aa0ff", verde: "#00ffa3", amarelo: "#ffd23f",
  };

  const $ = (id) => document.getElementById(id);
  let ultimoEstadoJSON = "";

  // ---------------------------------------------------------------------
  // Relogio (data e hora) — atualiza a cada segundo, independente do estado.
  // ---------------------------------------------------------------------
  function atualizarRelogio() {
    const agora = new Date();
    const p = (n) => String(n).padStart(2, "0");
    $("clock-time").textContent = `${p(agora.getHours())}:${p(agora.getMinutes())}:${p(agora.getSeconds())}`;
    $("clock-date").textContent = `${p(agora.getDate())}/${p(agora.getMonth() + 1)}/${agora.getFullYear()}`;
  }

  // ---------------------------------------------------------------------
  // Render do estado completo
  // ---------------------------------------------------------------------
  function render(s) {
    // Status
    const status = (s.status || "aguardando").toLowerCase();
    document.body.dataset.status = status;
    $("status-text").textContent = STATUS_LABEL[status] || status.toUpperCase();

    // Animacao de audio: ouvindo OU falando OU status que envolve voz
    const audioAtivo = !!s.listening || !!s.speaking ||
                       status === "ouvindo" || status === "respondendo";
    document.body.classList.toggle("audio-active", audioAtivo);

    // ----- Operador identificado pela voz (biometria local) -----
    const opNome = s.operator_name || "—";
    const opEl = $("op-name");
    if (opEl) {
      opEl.textContent = opNome;
      // destaca quando desconhecido
      const desconhecido = !opNome || opNome === "—" || opNome.toLowerCase() === "desconhecido";
      opEl.classList.toggle("op-unknown", desconhecido);
    }
    const opConfEl = $("op-conf");
    if (opConfEl) {
      const c = Number(s.operator_conf) || 0;
      opConfEl.textContent = c > 0 ? `${Math.round(c * 100)}%` : "";
    }

    // Resposta principal
    $("main-response").textContent = s.main_response || "—";

    // Texto transcrito (rodape + centro no modo PC)
    const falado = s.operator_text || "—";
    $("operator-text").textContent = falado;
    const faladoPc = $("operator-text-pc");
    if (faladoPc) faladoPc.textContent = falado;

    // ----- Maquina -----
    const m = s.machine || {};
    $("m-id").textContent = m.id || "—";
    $("m-status").textContent = m.status || "—";
    $("m-model").textContent = m.model || "—";
    renderExtrusores(m.extruders || []);

    // ----- Producao pendente / peca recomendada -----
    // Monta so com os campos que existem (evita "undefined" na TV).
    const r = s.recommended_part;
    if (r) {
      const extra = [];
      if (r.material) extra.push(r.material);
      if (r.color) extra.push(r.color);
      if (r.qty_pending != null) extra.push(`faltam ${r.qty_pending}`);
      if (r.extruder != null) extra.push(`extrusor ${r.extruder}`);
      $("recommended").textContent = extra.length
        ? `${r.name || "?"} — ${extra.join(" · ")}`
        : (r.name || "?");
    } else {
      $("recommended").textContent = "Nada pendente no momento.";
    }

    // ----- Ticket -----
    const t = s.ticket || {};
    $("t-id").textContent = t.id || "—";
    $("t-date").textContent = t.date ? formatarData(t.date) : "—";
    $("t-client").textContent = t.client || "—";

    // ----- Pecas faltantes -----
    renderFaltantes(s.missing_parts || []);

    // ----- Alertas -----
    renderAlertas(s.alerts || []);

    // ----- Historico -----
    renderHistorico(s.history || []);

    // ----- Log resumido -----
    const log = $("log-summary");
    if (log) {
      const quando = s.last_update ? formatarHora(s.last_update) : "—";
      log.textContent = `status=${status} | conf=${s.confidence || "?"} | atualizado=${quando}`;
    }
  }

  function renderExtrusores(lista) {
    const cont = $("extruders");
    cont.innerHTML = "";
    if (!lista.length) { cont.innerHTML = `<div class="alert-empty">Sem extrusores.</div>`; return; }
    lista.forEach((e) => {
      const cor = (e.color || "").toLowerCase();
      const div = document.createElement("div");
      div.className = "extruder";
      div.innerHTML =
        `<span class="ex-num">${e.id ?? "?"}</span>` +
        `<span class="ex-mat">${e.material || "?"} ${e.color || ""}</span>` +
        `<span class="swatch" style="background:${CORES[cor] || "#666"}"></span>`;
      cont.appendChild(div);
    });
  }

  function renderFaltantes(lista) {
    const cont = $("missing-parts");
    cont.innerHTML = "";
    if (!lista.length) { cont.innerHTML = `<div class="alert-empty">Nada pendente.</div>`; return; }
    lista.forEach((p) => {
      const div = document.createElement("div");
      div.className = "missing-item";
      div.innerHTML =
        `<span class="qty">${p.missing ?? "?"}</span>` +
        `<span class="pinfo"><span>${p.name || "?"}</span>` +
        `<small>${p.material || ""} ${p.color || ""}</small></span>`;
      cont.appendChild(div);
    });
  }

  function renderAlertas(lista) {
    const cont = $("alerts");
    cont.innerHTML = "";
    if (!lista.length) { cont.innerHTML = `<div class="alert-empty">Sem alertas.</div>`; return; }
    lista.forEach((a) => {
      const div = document.createElement("div");
      div.className = "alert-item";
      div.textContent = a;
      cont.appendChild(div);
    });
  }

  function renderHistorico(lista) {
    const ul = $("history");
    ul.innerHTML = "";
    // Mostra as mais recentes primeiro (limita a 5 para nao poluir a TV).
    lista.slice(-5).reverse().forEach((h) => {
      const li = document.createElement("li");
      li.innerHTML = `<b>${h.time || ""}</b>${h.text || ""}`;
      ul.appendChild(li);
    });
  }

  function formatarData(iso) {
    const partes = String(iso).split("-");
    return partes.length === 3 ? `${partes[2]}/${partes[1]}/${partes[0]}` : iso;
  }
  function formatarHora(iso) {
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleTimeString("pt-BR");
  }

  // ---------------------------------------------------------------------
  // Busca do estado (polling leve, re-render so quando muda)
  // ---------------------------------------------------------------------
  async function buscarEstado() {
    try {
      const resp = await fetch(STATE_URL + "?t=" + Date.now(), { cache: "no-store" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const texto = await resp.text();
      $("conn").classList.remove("off");
      if (texto !== ultimoEstadoJSON) {
        ultimoEstadoJSON = texto;
        render(JSON.parse(texto));
      }
    } catch (err) {
      // Offline / arquivo movido: marca o indicador e mantem o ultimo estado.
      $("conn").classList.add("off");
      // Aviso unico no console (sem floodar).
      if (!buscarEstado._avisou) {
        console.warn("Nao foi possivel ler o estado:", err.message,
          "\nDica: abra via servidor local (python dashboard/serve.py), nao via file://");
        buscarEstado._avisou = true;
      }
    }
  }

  // ---------------------------------------------------------------------
  // Modo TV / PC
  // ---------------------------------------------------------------------
  function aplicarModo(modo) {
    document.body.classList.remove("mode-tv", "mode-pc");
    document.body.classList.add(modo === "pc" ? "mode-pc" : "mode-tv");
    $("btn-mode").textContent = "Modo: " + (modo === "pc" ? "PC" : "TV");
    try { localStorage.setItem("jarvis_modo", modo); } catch (_) {}
  }

  function alternarModo() {
    const atual = document.body.classList.contains("mode-pc") ? "pc" : "tv";
    aplicarModo(atual === "pc" ? "tv" : "pc");
  }

  function modoInicial() {
    const url = new URLSearchParams(location.search).get("mode");
    if (url === "tv" || url === "pc") return url;
    try { return localStorage.getItem("jarvis_modo") || "tv"; } catch (_) { return "tv"; }
  }

  // ---------------------------------------------------------------------
  // Caixa de pergunta: envia ao backend (/api/ask), que processa e atualiza
  // o estado. Depois forcamos uma leitura imediata para a tela reagir na hora.
  // ---------------------------------------------------------------------
  async function enviarPergunta(evento) {
    if (evento) evento.preventDefault();
    const inp = $("ask-input");
    const pergunta = inp.value.trim();
    if (!pergunta) return;
    const falar = $("ask-falar").checked;

    inp.value = "";
    // feedback otimista: mostra "pensando" na hora
    document.body.dataset.status = "pensando";
    $("status-text").textContent = STATUS_LABEL["pensando"];

    try {
      await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pergunta, falar }),
      });
    } catch (err) {
      console.warn("Falha ao enviar a pergunta:", err.message);
    }
    // le o estado novo (o backend ja gravou o dashboard_state.json)
    ultimoEstadoJSON = "";   // forca re-render
    buscarEstado();
  }

  // ---------------------------------------------------------------------
  // Cadastro de voz (biometria): abre o modal, dispara a gravacao no backend
  // (que usa o mic da maquina) e faz polling do progresso.
  // ---------------------------------------------------------------------
  let vozPollTimer = null;

  function abrirVoz() {
    $("voz-overlay").hidden = false;
    $("voz-form").hidden = false;
    $("voz-progresso").hidden = true;
    $("voz-nome").value = "";
    $("voz-nome").focus();
    vozStatus();                       // mostra cadastrados na hora
    if (!vozPollTimer) vozPollTimer = setInterval(vozStatus, 900);
  }

  function fecharVoz() {
    $("voz-overlay").hidden = true;
    if (vozPollTimer) { clearInterval(vozPollTimer); vozPollTimer = null; }
  }

  async function iniciarVoz() {
    const nome = $("voz-nome").value.trim();
    if (!nome) { $("voz-nome").focus(); return; }
    $("voz-form").hidden = true;
    $("voz-progresso").hidden = false;
    $("voz-fase").textContent = "Preparando...";
    $("voz-frase").textContent = "—";
    $("voz-msg").textContent = "";
    try {
      const r = await fetch("/api/enroll", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nome }),
      });
      const d = await r.json();
      if (!d.ok) {
        $("voz-msg").textContent = "Não consegui iniciar: " + (d.mensagem || d.erro || "erro");
        $("voz-form").hidden = false;
        $("voz-progresso").hidden = true;
      }
    } catch (err) {
      $("voz-msg").textContent = "Falha ao iniciar: " + err.message;
      $("voz-form").hidden = false;
      $("voz-progresso").hidden = true;
    }
  }

  const FASE_LABEL = {
    idle: "Pronto", preparando: "Preparando...", ouvindo: "🎤 Ouvindo — fale agora",
    processando: "Processando a voz...", concluido: "✅ Cadastrado!", erro: "⚠ Erro",
  };

  async function vozStatus() {
    try {
      const r = await fetch("/api/enroll/status?t=" + Date.now(), { cache: "no-store" });
      const s = await r.json();

      // Lista de cadastrados
      const pessoas = s.pessoas || [];
      $("voz-pessoas").textContent = pessoas.length ? pessoas.join(", ") : "ninguém ainda";

      // So atualiza o progresso se o modal de progresso estiver visivel
      if ($("voz-progresso").hidden) return;

      $("voz-fase").textContent = FASE_LABEL[s.fase] || s.fase || "—";
      $("voz-frase").textContent = s.frase_atual || "—";
      $("voz-passos").textContent = s.total
        ? `Frase ${s.frase_idx || 0} de ${s.total} · ${s.capturadas || 0} captada(s)`
        : "";
      if (s.mensagem) $("voz-msg").textContent = s.mensagem;

      // indicador de microfone ativo
      $("voz-mic").classList.toggle("ativo", s.fase === "ouvindo");

      // Terminou (sucesso ou erro): volta pro formulario depois de mostrar o resultado
      if (s.fase === "concluido" || s.fase === "erro") {
        $("voz-mic").classList.remove("ativo");
        setTimeout(() => {
          if (!$("voz-overlay").hidden) {
            $("voz-form").hidden = false;
            $("voz-progresso").hidden = true;
            $("voz-nome").value = "";
          }
        }, 2500);
      }
    } catch (_) { /* servidor pode estar reiniciando — ignora */ }
  }

  function alternarTelaCheia() {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen?.().catch(() => {});
    } else {
      document.exitFullscreen?.();
    }
  }

  // ---------------------------------------------------------------------
  // Inicializacao
  // ---------------------------------------------------------------------
  function init() {
    aplicarModo(modoInicial());
    atualizarRelogio();
    buscarEstado();

    setInterval(atualizarRelogio, 1000);
    setInterval(buscarEstado, POLL_MS);

    $("btn-mode").addEventListener("click", alternarModo);
    $("btn-fs").addEventListener("click", alternarTelaCheia);
    $("ask-form").addEventListener("submit", enviarPergunta);

    // Cadastro de voz (modal)
    $("btn-voz").addEventListener("click", abrirVoz);
    $("voz-fechar").addEventListener("click", fecharVoz);
    $("voz-iniciar").addEventListener("click", iniciarVoz);
    $("voz-nome").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); iniciarVoz(); }
    });
    $("voz-overlay").addEventListener("click", (e) => {
      if (e.target === $("voz-overlay")) fecharVoz();   // clicar fora fecha
    });

    // Atalhos de teclado: M = modo, F = tela cheia.
    // Ignora quando o foco esta na caixa de texto (senao atrapalha a digitacao).
    document.addEventListener("keydown", (e) => {
      // Esc fecha o modal de voz
      if (e.key === "Escape" && !$("voz-overlay").hidden) { fecharVoz(); return; }
      // Ignora atalhos quando digitando OU com o modal aberto
      const digitando = e.target && (e.target.id === "ask-input" || e.target.id === "voz-nome");
      if (digitando || !$("voz-overlay").hidden) return;
      if (e.key === "m" || e.key === "M") alternarModo();
      if (e.key === "f" || e.key === "F") alternarTelaCheia();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
