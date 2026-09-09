// dashboard/app.js  (FASE 11)
// Le data/dashboard_state.json e atualiza o painel da JARVIS.
// Sem framework, sem internet. Atualiza por polling leve (a cada 1s) e so
// re-renderiza quando o estado muda (nao trava o PC).

(() => {
  "use strict";

  // Caminho do estado. Servido localmente: /dashboard/ -> ../data/...
  const STATE_URL = "../data/dashboard_state.json";
  const POLL_MS = 1000;

  // Maquina escolhida clicando num ponto do radar (sobrepoe a "visao geral"
  // ate o usuario clicar de novo pra voltar). ultimoEstado guarda o JSON
  // recem-lido pra poder re-renderizar na hora, sem esperar o proximo poll.
  let maquinaSelecionadaId = null;
  let ultimoEstado = null;

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

  // Troca o texto e, se ele mudou de verdade, retrigger um "flash" de brilho
  // (efeito HUD: chama atencao pra dado que acabou de atualizar).
  function setTextoComFlash(elOuId, texto) {
    const el = typeof elOuId === "string" ? $(elOuId) : elOuId;
    if (!el) return;
    const novo = texto ?? "—";
    if (el.textContent === String(novo)) return;
    el.textContent = novo;
    el.classList.remove("flash");
    void el.offsetWidth; // forca reflow pra animacao poder rodar de novo
    el.classList.add("flash");
  }

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
  // Temperatura de Goiania (Open-Meteo, gratis, sem chave de API).
  // Atualiza de vez em quando (o tempo nao muda a cada segundo).
  // ---------------------------------------------------------------------
  const GOIANIA_LAT = -16.6869;
  const GOIANIA_LON = -49.2648;
  const TEMP_POLL_MS = 15 * 60 * 1000; // 15 min

  async function atualizarTemperatura() {
    const el = $("clock-temp");
    if (!el) return;
    try {
      const url = `https://api.open-meteo.com/v1/forecast?latitude=${GOIANIA_LAT}&longitude=${GOIANIA_LON}&current=temperature_2m`;
      const resp = await fetch(url);
      if (!resp.ok) throw new Error("resposta " + resp.status);
      const dados = await resp.json();
      const t = dados?.current?.temperature_2m;
      if (typeof t !== "number") throw new Error("sem temperatura");
      el.textContent = `${Math.round(t)}°C Goiânia`;
      el.classList.remove("temp-erro");
    } catch (erro) {
      el.textContent = "-- °C";
      el.classList.add("temp-erro");
    }
  }

  // ---------------------------------------------------------------------
  // Render do estado completo
  // ---------------------------------------------------------------------
  function render(s) {
    // Status
    const status = (s.status || "aguardando").toLowerCase();
    document.body.dataset.status = status;
    setTextoComFlash("status-text", STATUS_LABEL[status] || status.toUpperCase());

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

    // Pulso vital: o HUD inteiro reage a producao REAL (nao decoracao —
    // quanto mais impressoras imprimindo, mais rapido o pulso/giro; alguma
    // em manutencao/offline tinge o nucleo de vermelho).
    aplicarPulsoVital(s.machines || []);
    renderEficiencia(s.machines || []);
    renderTendenciaProducao(s.production_history || []);
    renderResumoDia(s.daily_summary || null);

    // Resposta principal
    setTextoComFlash("main-response", s.main_response);

    // Texto transcrito (rodape + centro no modo PC)
    const falado = s.operator_text || "—";
    $("operator-text").textContent = falado;
    const faladoPc = $("operator-text-pc");
    if (faladoPc) faladoPc.textContent = falado;

    // ----- Maquina -----
    ultimoEstado = s;
    const listaMaquinas = s.machines || [];
    let m = s.machine || {};
    if (maquinaSelecionadaId) {
      const achada = listaMaquinas.find((x) => String(x.id) === maquinaSelecionadaId);
      if (achada) m = achada;
    }
    setTextoComFlash("m-id", m.id);
    setTextoComFlash("m-status", m.status);
    setTextoComFlash("m-model", m.model);
    renderExtrusores(m.extruders || []);
    renderVisualMaquina(m);
    renderRadarTodas(listaMaquinas);

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

  const STATUS_ICON = { imprimindo: "▶", ocioso: "■", manutencao: "⚠" };

  // O catos devolve status em ingles (printing/idle/offline...); o mock local
  // usa portugues (imprimindo/ocioso/manutencao). Normaliza os dois pros 3
  // "baldes" visuais do radar (cor + icone).
  const STATUS_BALDE = {
    imprimindo: "imprimindo", printing: "imprimindo", print: "imprimindo", printando: "imprimindo",
    ocioso: "ocioso", idle: "ocioso", online: "ocioso", available: "ocioso", disponivel: "ocioso",
    manutencao: "manutencao", maintenance: "manutencao", error: "manutencao",
    offline: "manutencao", erro: "manutencao", parada: "manutencao", stopped: "manutencao",
  };

  function baldeDeStatus(status) {
    return STATUS_BALDE[(status || "").toLowerCase()] || "desconhecido";
  }

  // Posicao deterministica (mesma maquina cai sempre no mesmo lugar do radar).
  function hashNum(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
    return h;
  }

  // ---------------------------------------------------------------------
  // Pulso vital: liga a "respiracao" do HUD central aos dados reais de
  // producao (nao e so decoracao — reflete quantas maquinas estao
  // imprimindo agora e se alguma precisa de atencao).
  // ---------------------------------------------------------------------
  function aplicarPulsoVital(lista) {
    const total = lista.length;
    const imprimindo = lista.filter((m) => baldeDeStatus(m.status) === "imprimindo").length;
    const emAlerta = lista.filter((m) => baldeDeStatus(m.status) === "manutencao").length;
    const taxa = total ? imprimindo / total : 0;

    // Mais atividade -> pulso e giro mais rapidos (HUD "acelera" com a produção).
    const pulseS = (2.4 - taxa * 1.5).toFixed(2) + "s";   // 2.4s parado -> 0.9s tudo imprimindo
    const spinS = (16 - taxa * 9).toFixed(1) + "s";        // 16s parado -> 7s tudo imprimindo
    document.documentElement.style.setProperty("--pulse", pulseS);
    document.documentElement.style.setProperty("--spin-slow", spinS);
    document.body.classList.toggle("hud-alerta", emAlerta > 0);

    const painel = $("pulso-vital");
    if (painel) {
      painel.classList.toggle("parado", total > 0 && imprimindo === 0);
      painel.classList.toggle("alerta", emAlerta > 0);
    }
    setTextoComFlash("pulso-texto",
      total ? `${imprimindo} / ${total} IMPRIMINDO${emAlerta ? ` — ${emAlerta} EM MANUTENÇÃO` : ""}` : "-- / -- IMPRIMINDO");
  }

  // ---------------------------------------------------------------------
  // Painel de eficiencia: % da frota imprimindo/ociosa/manutencao agora.
  // ---------------------------------------------------------------------
  function renderEficiencia(lista) {
    const total = lista.length;
    const conta = { imprimindo: 0, ocioso: 0, manutencao: 0, desconhecido: 0 };
    lista.forEach((m) => {
      const b = baldeDeStatus(m.status);
      conta[b] = (conta[b] || 0) + 1;
    });

    const pct = (n) => (total ? (n / total) * 100 : 0);
    const segs = {
      "eff-seg-imprimindo": pct(conta.imprimindo),
      "eff-seg-ocioso": pct(conta.ocioso),
      "eff-seg-manutencao": pct(conta.manutencao),
      "eff-seg-desconhecido": pct(conta.desconhecido),
    };
    Object.entries(segs).forEach(([id, largura]) => {
      const el = $(id);
      if (el) el.style.width = largura.toFixed(1) + "%";
    });

    const nImp = $("eff-n-imprimindo");
    const nOci = $("eff-n-ocioso");
    const nMan = $("eff-n-manutencao");
    if (nImp) nImp.textContent = conta.imprimindo;
    if (nOci) nOci.textContent = conta.ocioso;
    if (nMan) nMan.textContent = conta.manutencao;

    setTextoComFlash("eff-pct", total ? Math.round(pct(conta.imprimindo)) + "%" : "0%");
  }

  // ---------------------------------------------------------------------
  // Sparkline de tendencia: quantas maquinas estavam imprimindo na ultima
  // hora (amostras que a JARVIS guarda a cada refresh, ~1/min).
  // ---------------------------------------------------------------------
  function renderTendenciaProducao(historico) {
    const linha = $("eff-spark-line");
    const preenchido = $("eff-spark-fill");
    if (!linha || !preenchido) return;

    const pontos = Array.isArray(historico) ? historico : [];
    if (pontos.length < 2) {
      linha.setAttribute("points", "");
      preenchido.setAttribute("points", "");
      return;
    }

    const W = 200, H = 40, PAD = 3;
    const maxTotal = Math.max(1, ...pontos.map((p) => p.total || 0));
    const n = pontos.length;
    const coords = pontos.map((p, i) => {
      const x = n === 1 ? W : (i / (n - 1)) * W;
      const taxa = maxTotal ? (p.imprimindo || 0) / maxTotal : 0;
      const y = H - PAD - taxa * (H - PAD * 2);
      return [x, y];
    });

    linha.setAttribute("points", coords.map(([x, y]) => `${x},${y}`).join(" "));
    preenchido.setAttribute(
      "points",
      `0,${H} ` + coords.map(([x, y]) => `${x},${y}`).join(" ") + ` ${W},${H}`
    );
  }

  // ---------------------------------------------------------------------
  // Resumo do dia: card proativo (core/resumo_diario.py gera 1x por dia).
  // Pode ser dispensado — fica escondido ate o texto mudar (dia seguinte).
  // ---------------------------------------------------------------------
  function renderResumoDia(resumo) {
    const card = $("resumo-dia");
    if (!card) return;

    if (!resumo || !resumo.texto) {
      card.hidden = true;
      return;
    }

    let dispensado = "";
    try { dispensado = localStorage.getItem("jarvis_resumo_dispensado") || ""; } catch (_) {}
    if (dispensado === resumo.texto) {
      card.hidden = true;
      return;
    }

    $("resumo-dia-texto").textContent = resumo.texto;
    card.hidden = false;
  }

  function renderVisualMaquina(m) {
    const balde = baldeDeStatus(m.status);
    const ring = $("m-ring");
    if (ring) ring.className = "status-ring status-" + balde;
    const icon = $("m-ring-icon");
    if (icon) icon.textContent = STATUS_ICON[balde] || "?";

    const radar = $("m-radar");
    if (radar) radar.className = "radar status-" + balde;

    const vol = m.build_volume_mm;
    const label = $("m-vol-label");
    label && (label.textContent = (vol && vol.x && vol.y && vol.z)
      ? `${vol.x} × ${vol.y} × ${vol.z} mm`
      : "-- × -- × -- mm");
  }

  // Blips PERSISTEM entre renders (por id da maquina) — em vez de destruir e
  // recriar tudo a cada poll, so atualizamos posicao/cor dos que ja existem.
  // Isso deixa o radar FLUIDO: o ponto deslila pra nova posicao/cor (transition
  // no CSS) em vez de "piscar" do zero a cada segundo.
  const blipsPorId = new Map();

  function renderRadarTodas(lista) {
    const radar = $("m-radar");
    if (!radar) return;
    const total = (lista || []).length || 1;
    const vistos = new Set();

    (lista || []).forEach((m, i) => {
      const id = String(m.id || "?");
      vistos.add(id);
      const balde = baldeDeStatus(m.status);
      const h = hashNum(id);
      // Distribui por indice (espalha mesmo com IDs parecidos, ex.: M04..M10)
      // e usa o hash so pra um leve "jitter" no angulo/raio, pra nao ficar
      // um relogio perfeito.
      const anguloBase = (360 / total) * i + (h % 40) - 20;
      const angulo = anguloBase * Math.PI / 180;
      const raio = 18 + (h % 24); // 18% a 42% do centro (fica dentro do circulo)
      const cx = 50 + raio * Math.cos(angulo);
      const cy = 50 + raio * Math.sin(angulo);

      let blip = blipsPorId.get(id);
      if (!blip) {
        blip = document.createElement("div");
        blip.addEventListener("click", () => {
          maquinaSelecionadaId = (maquinaSelecionadaId === id) ? null : id;
          if (ultimoEstado) render(ultimoEstado);
        });
        // Le sempre o dado MAIS RECENTE (blip._maquina), nao o da criacao —
        // o mesmo elemento fica de pe por varios renders.
        blip.addEventListener("mouseenter", (ev) => mostrarTooltipRadar(blip._maquina, ev));
        blip.addEventListener("mousemove", (ev) => posicionarTooltipRadar(ev));
        blip.addEventListener("mouseleave", esconderTooltipRadar);

        const rotulo = document.createElement("span");
        rotulo.className = "radar-blip-label";
        rotulo.textContent = id;
        blip.appendChild(rotulo);

        radar.appendChild(blip);
        blipsPorId.set(id, blip);
      }

      blip._maquina = m;
      blip.className = "radar-blip status-" + balde
        + (id === maquinaSelecionadaId ? " selecionado" : "");
      blip.style.left = cx + "%";
      blip.style.top = cy + "%";
    });

    // Maquina que saiu da lista (ex.: catos parou de reportar): some o blip.
    for (const [id, blip] of blipsPorId) {
      if (!vistos.has(id)) {
        blip.remove();
        blipsPorId.delete(id);
      }
    }
  }

  // ---------------------------------------------------------------------
  // Tooltip rico do radar (card flutuante com detalhes da maquina).
  // ---------------------------------------------------------------------
  function mostrarTooltipRadar(m, ev) {
    const tip = $("radar-tooltip");
    if (!tip) return;
    const extrusores = (m.extruders || [])
      .map((e) => `${e.material || "?"}${e.color ? " " + e.color : ""}`)
      .join(" · ") || "sem extrusores";
    tip.innerHTML =
      `<div class="rt-titulo">${m.id || "?"}</div>` +
      `<div class="rt-linha">status: <b>${m.status || "?"}</b></div>` +
      (m.model ? `<div class="rt-linha">modelo: <b>${m.model}</b></div>` : "") +
      `<div class="rt-linha">extrusores: <b>${extrusores}</b></div>`;
    tip.hidden = false;
    posicionarTooltipRadar(ev);
    requestAnimationFrame(() => tip.classList.add("show"));
  }

  function posicionarTooltipRadar(ev) {
    const tip = $("radar-tooltip");
    if (!tip || tip.hidden) return;
    const margem = 14;
    let x = ev.clientX + margem;
    let y = ev.clientY + margem;
    const largura = tip.offsetWidth || 200;
    const altura = tip.offsetHeight || 60;
    if (x + largura > window.innerWidth) x = ev.clientX - largura - margem;
    if (y + altura > window.innerHeight) y = ev.clientY - altura - margem;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }

  function esconderTooltipRadar() {
    const tip = $("radar-tooltip");
    if (!tip) return;
    tip.classList.remove("show");
    tip.hidden = true;
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
  let falhasConsecutivas = 0;
  const FALHAS_PARA_BANNER = 3; // ~3s de conexao perdida antes de alarmar (evita flicker por 1 blip)

  async function buscarEstado() {
    try {
      const resp = await fetch(STATE_URL + "?t=" + Date.now(), { cache: "no-store" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const texto = await resp.text();
      $("conn").classList.remove("off");
      falhasConsecutivas = 0;
      $("conn-banner").hidden = true;
      buscarEstado._avisou = false;
      if (texto !== ultimoEstadoJSON) {
        ultimoEstadoJSON = texto;
        render(JSON.parse(texto));
      }
    } catch (err) {
      // Offline / arquivo movido: marca o indicador e mantem o ultimo estado.
      $("conn").classList.add("off");
      falhasConsecutivas++;
      if (falhasConsecutivas >= FALHAS_PARA_BANNER) {
        $("conn-banner").hidden = false;
      }
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
    const ouvirAqui = $("ask-ouvir") && $("ask-ouvir").checked;

    inp.value = "";
    // feedback otimista: mostra "pensando" na hora
    document.body.dataset.status = "pensando";
    $("status-text").textContent = STATUS_LABEL["pensando"];

    // Desbloqueia o audio DENTRO do gesto do usuario (envio do form). Sem isto,
    // celulares (iOS/Android) bloqueiam o play() que acontece depois do fetch.
    if (ouvirAqui) desbloquearAudio();

    let resposta = "";
    try {
      const r = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pergunta, falar }),
      });
      try { const d = await r.json(); resposta = (d && d.resposta) || ""; } catch (_) {}
    } catch (err) {
      console.warn("Falha ao enviar a pergunta:", err.message);
    }
    // le o estado novo (o backend ja gravou o dashboard_state.json)
    ultimoEstadoJSON = "";   // forca re-render
    buscarEstado();

    // Toca a resposta NESTE aparelho (celular/PC que acessa), se pedido.
    if (ouvirAqui && resposta) tocarTTS(resposta);
  }

  // ---------------------------------------------------------------------
  // Audio no proprio aparelho (celular/PC). Pede o MP3 ao backend (/api/tts)
  // — mesma voz neural do JARVIS — e toca aqui, sem depender do alto-falante
  // do PC do lab. Reusa UM elemento <audio> destravado no gesto do usuario.
  // ---------------------------------------------------------------------
  // WAV silencioso (0 amostras): tocado no clique para liberar o autoplay.
  const SILENCIO = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAAAAA=";
  let audioEl = null;

  function garantirAudioEl() {
    if (!audioEl) audioEl = new Audio();
    return audioEl;
  }

  function desbloquearAudio() {
    const a = garantirAudioEl();
    try {
      a.src = SILENCIO;
      const p = a.play();
      if (p && p.catch) p.catch(() => {});
    } catch (_) {}
  }

  async function tocarTTS(texto) {
    const a = garantirAudioEl();
    try {
      const r = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texto }),
      });
      if (!r.ok) { console.warn("TTS indisponível:", r.status); return; }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      a.src = url;
      document.body.classList.add("audio-active");
      a.onended = () => {
        document.body.classList.remove("audio-active");
        URL.revokeObjectURL(url);
      };
      await a.play();
    } catch (err) {
      console.warn("Não consegui tocar o áudio aqui:", err.message);
    }
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
    atualizarTemperatura();

    setInterval(atualizarRelogio, 1000);
    setInterval(buscarEstado, POLL_MS);
    setInterval(atualizarTemperatura, TEMP_POLL_MS);

    $("btn-mode").addEventListener("click", alternarModo);
    $("btn-fs").addEventListener("click", alternarTelaCheia);
    $("ask-form").addEventListener("submit", enviarPergunta);

    const btnFecharResumo = $("resumo-dia-fechar");
    if (btnFecharResumo) {
      btnFecharResumo.addEventListener("click", () => {
        const texto = $("resumo-dia-texto").textContent;
        try { localStorage.setItem("jarvis_resumo_dispensado", texto); } catch (_) {}
        $("resumo-dia").hidden = true;
      });
    }

    // Lembra a preferencia de "ouvir aqui" por aparelho (fica marcado no celular).
    const chkOuvir = $("ask-ouvir");
    if (chkOuvir) {
      try { chkOuvir.checked = localStorage.getItem("jarvis_ouvir") === "1"; } catch (_) {}
      chkOuvir.addEventListener("change", () => {
        try { localStorage.setItem("jarvis_ouvir", chkOuvir.checked ? "1" : "0"); } catch (_) {}
        if (chkOuvir.checked) desbloquearAudio();  // destrava no gesto de marcar
      });
    }

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

  // Registra o service worker (necessário para instalar como app no Android).
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("sw.js")
        .then(configurarNotificacoesPush)
        .catch((err) => {
          console.warn("Service worker não registrado:", err.message);
        });
    });
  }

  // ---------------------------------------------------------------------
  // Notificacoes push (chegam com o app fechado no celular). So aparece o
  // botao se o navegador suporta E o servidor tem push configurado.
  // ---------------------------------------------------------------------
  function urlBase64ToUint8Array(base64) {
    const padding = "=".repeat((4 - (base64.length % 4)) % 4);
    const b64 = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
    const bruto = atob(b64);
    return Uint8Array.from([...bruto].map((c) => c.charCodeAt(0)));
  }

  async function configurarNotificacoesPush(registro) {
    if (!("PushManager" in window) || !("Notification" in window)) return;

    let info;
    try {
      const resp = await fetch("/api/push/vapid_public");
      info = await resp.json();
    } catch (_) { return; }
    if (!info || !info.disponivel || !info.chave) return;

    const btn = $("btn-push");
    if (!btn) return;
    btn.hidden = false;

    const atualizarTextoBotao = () => {
      const inscrito = Notification.permission === "granted";
      btn.textContent = inscrito ? "🔔 Notificações ativas" : "🔔 Notificações";
      btn.classList.toggle("ativo", inscrito);
    };
    atualizarTextoBotao();

    btn.addEventListener("click", async () => {
      try {
        const permissao = await Notification.requestPermission();
        if (permissao !== "granted") {
          atualizarTextoBotao();
          return;
        }
        const subscricao = await registro.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(info.chave),
        });
        await fetch("/api/push/subscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(subscricao.toJSON()),
        });
        atualizarTextoBotao();
      } catch (err) {
        console.warn("Falha ao ativar notificacoes push:", err.message);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
