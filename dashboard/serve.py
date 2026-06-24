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


class HandlerSemCache(SimpleHTTPRequestHandler):
    """Serve os arquivos sem cache (para o polling sempre ver o estado novo)."""

    def do_GET(self):
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
        super().do_GET()

    def do_POST(self):
        # Pergunta enviada pela caixa de texto do painel.
        if self.path == "/api/ask":
            self._tratar_ask()
            return
        # Cadastro de voz (biometria): inicia a gravacao guiada.
        if self.path == "/api/enroll":
            self._tratar_enroll()
            return
        if self.path == "/api/enroll/remover":
            self._tratar_enroll_remover()
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


_REFRESH_INTERVALO_S = 60  # atualiza o painel com dados frescos do sistema a cada 60s


def _refresh_overview() -> None:
    """
    Atualiza o painel em background com dados frescos do sistema (overview geral),
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
