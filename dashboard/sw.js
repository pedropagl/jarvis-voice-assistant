// dashboard/sw.js
// Service worker mínimo do JARVIS.
// Objetivo: tornar o painel INSTALÁVEL como app (Android/Chrome exige um SW com
// handler de fetch). NÃO faz cache: o JARVIS depende do servidor (dados ao vivo
// + login), então servir conteúdo salvo poderia mostrar dado velho ou quebrar a
// autenticação. Aqui só repassamos tudo para a rede.

self.addEventListener("install", (e) => {
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (e) => {
  // Passagem direta para a rede (sem cache).
  e.respondWith(fetch(e.request));
});

// Notificacao push: chega mesmo com o app fechado (dashboard/app.js cuida
// de pedir permissao e se inscrever; o backend manda o push via core/push.py).
self.addEventListener("push", (e) => {
  let dados = { titulo: "JARVIS", corpo: "" };
  try {
    dados = e.data ? e.data.json() : dados;
  } catch (_) {
    dados.corpo = e.data ? e.data.text() : "";
  }
  e.waitUntil(
    self.registration.showNotification(dados.titulo || "JARVIS", {
      body: dados.corpo || "",
      icon: "icon-192.png",
      badge: "icon-192.png",
      tag: "jarvis-alerta",
    })
  );
});

// Clicar na notificacao abre (ou foca) o painel.
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(
    self.clients.matchAll({ type: "window" }).then((lista) => {
      for (const cliente of lista) {
        if ("focus" in cliente) return cliente.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow("/dashboard/index.html");
    })
  );
});
