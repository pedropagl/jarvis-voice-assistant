"""
dashboard/serve.py  (FASE 11)
-----------------------------
Servidor local minimo para o painel da JARVIS.

Por que existe:
    Navegadores bloqueiam fetch() de arquivos via file:// (politica de seguranca).
    Este servidor (biblioteca padrao do Python, SEM dependencias) entrega o
    painel e o dashboard_state.json pela rede local. 100% offline.

Como usar (CMD, com o venv ativo):
    python dashboard/serve.py
    Depois abra no navegador:  http://127.0.0.1:8000/dashboard/index.html

Tambem aceita perguntas pela PROPRIA interface (caixa de texto no painel):
    POST /api/ask  {"pergunta": "...", "falar": true|false}
    -> processa pelo cerebro, atualiza o painel e (opcional) fala a resposta.

Host/porta vem do .env (DASHBOARD_HOST / DASHBOARD_PORT), com padrao 127.0.0.1:8000.
"""

import base64
import hmac
import json
import os
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Raiz do projeto (pai da pasta dashboard/). Servimos a raiz para que tanto
# /dashboard/ quanto /data/ fiquem acessiveis.
RAIZ = Path(__file__).resolve().parent.parent

# Permite importar os modulos do projeto (core/, company_system/) ao rodar
# "python dashboard/serve.py".
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv
    load_dotenv(RAIZ / ".env")
except ImportError:
    pass

HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
# PORT (atribuida por ferramentas de preview) tem prioridade; senao DASHBOARD_PORT; senao 8000.
PORT = int(os.getenv("PORT") or os.getenv("DASHBOARD_PORT") or "8000")

# ------------------------------------------------------------------ #
# Senha do painel (protege o acesso externo via tunel Cloudflare).
# Vem do .env. Se PAINEL_SENHA estiver VAZIA, a autenticacao fica
# DESLIGADA (uso 100% local, sem exposicao). Quando o painel e exposto
# pela internet, defina PAINEL_SENHA no .env para exigir login.
# ------------------------------------------------------------------ #
PAINEL_USUARIO = os.getenv("PAINEL_USUARIO", "jarvis")
PAINEL_SENHA = os.getenv("PAINEL_SENHA", "")


class HandlerSemCache(SimpleHTTPRequestHandler):
    """Serve os arquivos sem cache (para o polling sempre ver o estado novo)."""

    def _auth_ok(self) -> bool:
        """
        True se a requisicao trouxe credenciais validas (HTTP Basic Auth).
        Se PAINEL_SENHA estiver vazia no .env, retorna sempre True (login OFF).
        Comparacao em tempo constante (hmac) para nao vazar a senha por timing.
        """
        if not PAINEL_SENHA:
            return True  # sem senha configurada -> painel aberto (uso local)
        cabecalho = self.headers.get("Authorization", "")
        if not cabecalho.startswith("Basic "):
            return False
        try:
            cru = base64.b64decode(cabecalho[6:]).decode("utf-8")
            usuario, _, senha = cru.partition(":")
        except Exception:  # noqa: BLE001
            return False
        ok_user = hmac.compare_digest(usuario, PAINEL_USUARIO)
        ok_senha = hmac.compare_digest(senha, PAINEL_SENHA)
        return ok_user and ok_senha

    def _pedir_login(self) -> None:
        """Responde 401 e dispara a janela de login nativa do navegador."""
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="JARVIS"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Acesso restrito. Faca login para ver o painel.".encode("utf-8"))

    # Arquivos do PWA liberados SEM senha: o navegador busca o manifest, os
    # ícones e o service worker "por fora" (sem mandar as credenciais) na hora
    # de instalar o app. Se exigissem login, o ícone do JARVIS não seria baixado
    # e o celular cairia num ícone genérico. Nada aqui é sigiloso.
    _PWA_PUBLICO = (
        "/dashboard/manifest.webmanifest",
        "/dashboard/sw.js",
        "/dashboard/icon-192.png",
        "/dashboard/icon-512.png",
    )

    def do_GET(self):
        caminho = self.path.split("?", 1)[0]
        if caminho not in self._PWA_PUBLICO and not self._auth_ok():
            self._pedir_login()
            return
        # Atalho: abrir a raiz "/" leva direto ao painel.
        if self.path in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/dashboard/index.html")
            self.end_headers()
            return
        # Status do cadastro de voz (polling do painel).
        if self.path.startswith("/api/enroll/status"):
            try:
                from core import cadastro_voz
                self._responder_json(cadastro_voz.status())
            except Exception as erro:  # noqa: BLE001
                self._responder_json({"erro": str(erro)}, 500)
            return
        # Chave publica VAPID: o front-end usa pra assinar a inscricao de push.
        if self.path == "/api/push/vapid_public":
            try:
                from core import push
                self._responder_json({"chave": push.chave_publica(), "disponivel": push.disponivel()})
            except Exception as erro:  # noqa: BLE001
                self._responder_json({"erro": str(erro)}, 500)
            return
        super().do_GET()

    def do_POST(self):
        # Webhook da Alexa: NAO usa Basic Auth (a Alexa nao manda login).
        # A protecao e por applicationId + timestamp dentro do core.alexa.
        if self.path == "/alexa/webhook":
            self._tratar_alexa()
            return
        if not self._auth_ok():
            self._pedir_login()
            return
        # Pergunta enviada pela caixa de texto do painel.
        if self.path == "/api/ask":
            self._tratar_ask()
            return
        # Text-to-Speech para o navegador (celular/PC): devolve o MP3 da fala.
        if self.path == "/api/tts":
            self._tratar_tts()
            return
        # Cadastro de voz (biometria): inicia a gravacao guiada.
        if self.path == "/api/enroll":
            self._tratar_enroll()
            return
        if self.path == "/api/enroll/remover":
            self._tratar_enroll_remover()
            return
        # Inscricao/cancelamento de notificacao push do painel (PWA).
        if self.path == "/api/push/subscribe":
            try:
                from core import push
                push.registrar_subscricao(self._ler_json())
                self._responder_json({"ok": True})
            except Exception as erro:  # noqa: BLE001
                self._responder_json({"erro": str(erro)}, 500)
            return
        if self.path == "/api/push/unsubscribe":
            try:
                from core import push
                dados = self._ler_json()
                push.remover_subscricao(dados.get("endpoint", ""))
                self._responder_json({"ok": True})
            except Exception as erro:  # noqa: BLE001
                self._responder_json({"erro": str(erro)}, 500)
            return
        self.send_error(404)

    def _ler_json(self) -> dict:
        tamanho = int(self.headers.get("Content-Length", 0) or 0)
        corpo = self.rfile.read(tamanho) if tamanho else b"{}"
        try:
            return json.loads(corpo.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            return {}

    def _responder_json(self, dados: dict, status: int = 200) -> None:
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _tratar_alexa(self):
        """Recebe o request da Alexa (ASK), roteia pra JARVIS e devolve o JSON."""
        req = self._ler_json()
        try:
            from core import alexa, painel
            corpo, status = alexa.processar_request(req)
            # Reflete no painel do lab o que foi perguntado pela Alexa (contexto
            # visual pra quem esta na frente da tela), sem misturar historico.
            try:
                fala = ((req.get("request", {}) or {}).get("intent", {}) or {}) \
                    .get("slots", {}).get("texto", {}).get("value")
                resp = (corpo.get("response", {}).get("outputSpeech", {}) or {}).get("text")
                if fala and resp:
                    painel.atualizar(f"(Alexa) {fala}", {"resposta": resp, "intencao": "alexa"})
            except Exception:  # noqa: BLE001
                pass
        except Exception as erro:  # noqa: BLE001
            corpo = {"version": "1.0", "response": {
                "outputSpeech": {"type": "PlainText",
                                 "text": "Tive um problema agora. Tenta de novo."},
                "shouldEndSession": True}}
            status = 200
            from core.logger import get_logger
            get_logger("jarvis.alexa").error("falha no webhook: %s", erro)
        self._responder_json(corpo, status)

    def _tratar_enroll(self):
        """Inicia o cadastro de voz de uma pessoa (grava pelo mic da maquina)."""
        dados = self._ler_json()
        nome = (dados.get("nome") or "").strip()
        try:
            from core import cadastro_voz
            ok, msg = cadastro_voz.iniciar(nome)
            self._responder_json({"ok": ok, "mensagem": msg})
        except Exception as erro:  # noqa: BLE001
            self._responder_json({"ok": False, "erro": str(erro)}, 500)

    def _tratar_enroll_remover(self):
        dados = self._ler_json()
        nome = (dados.get("nome") or "").strip()
        try:
            from core import cadastro_voz
            ok, msg = cadastro_voz.remover(nome)
            self._responder_json({"ok": ok, "mensagem": msg})
        except Exception as erro:  # noqa: BLE001
            self._responder_json({"ok": False, "erro": str(erro)}, 500)

    def _tratar_tts(self):
        """
        Gera o audio (MP3) da fala e devolve ao navegador para tocar no proprio
        aparelho (celular/PC de quem acessa). Diferente do "falar", que toca no
        alto-falante do PC do lab. 100% pela mesma voz neural do JARVIS.
        """
        dados = self._ler_json()
        texto = (dados.get("texto") or "").strip()
        if not texto:
            self._responder_json({"ok": False, "erro": "texto vazio"}, 400)
            return
        try:
            from core import voice_output
            audio = voice_output.sintetizar_mp3_bytes(texto)
        except Exception as erro:  # noqa: BLE001
            self._responder_json({"ok": False, "erro": str(erro)}, 500)
            return
        if not audio:
            self._responder_json({"ok": False, "erro": "tts indisponivel"}, 503)
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)

    def _tratar_ask(self):
        tamanho = int(self.headers.get("Content-Length", 0) or 0)
        corpo = self.rfile.read(tamanho) if tamanho else b"{}"
        try:
            dados = json.loads(corpo.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            dados = {}

        pergunta = (dados.get("pergunta") or "").strip()
        falar = bool(dados.get("falar"))

        resposta = {"ok": False, "resposta": ""}
        if pergunta:
            try:
                from core import gemini_brain, painel, voice_output
                painel.set_status("pensando")
                resultado = gemini_brain.processar(pergunta)
                painel.atualizar(pergunta, resultado)   # atualiza a tela ao vivo
                if falar:
                    voice_output.speak(resultado["resposta"], bloqueante=False)
                resposta = {"ok": True, "resposta": resultado.get("resposta", ""),
                            "intencao": resultado.get("intencao")}
            except Exception as erro:  # noqa: BLE001
                resposta = {"ok": False, "erro": str(erro)}

        corpo_resp = json.dumps(resposta, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo_resp)))
        self.end_headers()
        self.wfile.write(corpo_resp)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def log_message(self, *args):
        pass  # silencia o log de cada requisicao (poll a cada 1s polui o terminal)


_REFRESH_INTERVALO_S = 60  # atualiza o painel com dados frescos do catos a cada 60s


def _refresh_overview() -> None:
    """
    Atualiza o painel em background com dados frescos do catos (overview geral),
    mesmo sem nenhum comando de voz. Roda em daemon thread — nunca trava o servidor.
    """
    # Aguarda um pouco antes do primeiro ciclo para o servidor subir por completo.
    time.sleep(10)
    while True:
        try:
            from core import painel
            # So atualiza os paineis de dados; preserva a ultima resposta na tela.
            painel.refresh_dados()
        except Exception:  # noqa: BLE001 - refresh nunca derruba o servidor
            pass
        try:
            from core import relatorio_quinzenal
            # Checagem barata (so le uma data); so gera/envia de fato a cada 14 dias.
            relatorio_quinzenal.verificar_e_enviar()
        except Exception:  # noqa: BLE001 - relatorio nunca derruba o servidor
            pass
        try:
            from core import resumo_diario
            # Checagem barata (so le uma data); so gera/publica de fato 1x por dia.
            resumo_diario.gerar_e_publicar()
        except Exception:  # noqa: BLE001 - resumo nunca derruba o servidor
            pass
        try:
            from core import memoria
            # Limpeza barata (so roda DELETE se houver algo vencido).
            memoria.limpar_fatos_vencidos()
        except Exception:  # noqa: BLE001 - limpeza nunca derruba o servidor
            pass
        time.sleep(_REFRESH_INTERVALO_S)


def main():
    handler = partial(HandlerSemCache, directory=str(RAIZ))
    servidor = ThreadingHTTPServer((HOST, PORT), handler)
    url = f"http://{HOST}:{PORT}/dashboard/index.html"
    print("=" * 60)
    print("  Painel da JARVIS rodando (Ctrl+C para parar)")
    print(f"  Abra no navegador: {url}")
    print(f"  Modo TV direto:    {url}?mode=tv")
    print(f"  Modo PC direto:    {url}?mode=pc")
    print("=" * 60)

    # Inicia o refresh automatico em background (dados frescos a cada 60s).
    threading.Thread(target=_refresh_overview, daemon=True, name="painel-refresh").start()

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nPainel encerrado.")
        servidor.shutdown()


if __name__ == "__main__":
    main()
