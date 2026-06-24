"""
dashboard/demo_estados.py  (FASE 11 - auxiliar de teste)
--------------------------------------------------------
Reescreve data/dashboard_state.json em ciclo, trocando o "status" a cada poucos
segundos, para voce VER as animacoes e cores do painel mudando ao vivo.

Nao faz parte do fluxo da JARVIS — e so uma ferramenta de demonstracao da Fase 11.
A integracao real (a JARVIS escrevendo o estado) vem na Fase 12.

Como usar (em outro terminal, com o serve.py ja rodando):
    python dashboard/demo_estados.py
Depois olhe o navegador: o status, as cores e as animacoes vao alternar.
Ctrl+C para parar.
"""

import json
import time
from datetime import datetime
from pathlib import Path

ESTADO = Path(__file__).resolve().parent.parent / "data" / "dashboard_state.json"

# Sequencia de status que simula um ciclo de atendimento da JARVIS.
CICLO = [
    ("aguardando",          False, False, "Aguardando comando."),
    ("ouvindo",             True,  False, "Ouvindo o operador..."),
    ("transcrevendo",       False, False, "Transcrevendo a fala..."),
    ("consultando",         False, False, "Consultando o sistema..."),
    ("pensando",            False, False, "Analisando os dados..."),
    ("respondendo",         False, True,  "Para o ticket do dia 23 de maio, recomendo fazer capa protetora na M04, pois faltam 3 unidades e ela ja esta com APEX vermelho no extrusor 1."),
    ("dados_insuficientes", False, False, "Nao encontrei informacao suficiente. Pode me dizer a maquina ou o ticket?"),
    ("erro",                False, False, "Ocorreu um erro ao consultar o sistema."),
]


def main():
    base = json.loads(ESTADO.read_text(encoding="utf-8"))
    print("Alternando estados a cada 3s. Ctrl+C para parar.")
    i = 0
    try:
        while True:
            status, ouvindo, falando, resposta = CICLO[i % len(CICLO)]
            base["status"] = status
            base["listening"] = ouvindo
            base["speaking"] = falando
            base["main_response"] = resposta
            base["last_update"] = datetime.now().isoformat(timespec="seconds")
            ESTADO.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  status -> {status}")
            i += 1
            time.sleep(3)
    except KeyboardInterrupt:
        print("\nDemo encerrada.")


if __name__ == "__main__":
    main()
