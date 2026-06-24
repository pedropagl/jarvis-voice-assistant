# JARVIS — Assistente de Voz para Laboratório de Impressão 3D

Assistente de voz com IA desenvolvido para o laboratório de impressão 3D
do Grupo Odilon Santos. Integrado ao sistema de produção real da empresa
via protocolo MCP (Model Context Protocol).

## O problema

O laboratório opera ~8 impressoras 3D simultaneamente. Consultar o status
de uma máquina, verificar tickets abertos ou agendar uma impressão exigia
acessar o sistema manualmente — interrompendo o fluxo de trabalho do operador.

## O que o JARVIS faz

- Responde perguntas por voz em tempo real ("qual o status da M04?",
  "quais peças estão faltando no ticket 312?")
- Executa ações no sistema de produção com confirmação obrigatória
  (agendar mesa, enviar job para impressora, registrar resultado)
- Identifica o operador por **biometria de voz** (100% local, sem dados
  enviados para nuvem) para rastreabilidade de cada ação
- Exibe um **dashboard ao vivo** em TV no laboratório com status das
  máquinas, tickets urgentes e produção pendente
- Registra métricas de impacto em background (tempo economizado vs.
  processo manual)

## Arquitetura
Microfone → Transcrição (Whisper local)
→ Wake word ("Jarvis")
→ LLM via API (Kilo/Gemini)
→ Ferramentas MCP (leitura + 4 ações liberadas)
→ Resposta em voz (TTS local)
→ Dashboard HTML/JS (atualizado em tempo real)
→ SQLite (métricas de impacto em background)

## Stack

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.13 |
| LLM | Gemini via Kilo API |
| Integração | MCP (Model Context Protocol) |
| Biometria de voz | webrtcvad + embeddings locais |
| Transcrição | Whisper (local) |
| Dashboard | HTML + JS + CSS (sem framework) |
| Métricas | SQLite nativo |
| Deploy | Windows 11, execução local |

## Segurança

- Credenciais apenas no `.env`, nunca no código
- Separação estrita entre ferramentas de leitura e ações de escrita
- Ações de escrita bloqueadas por padrão — apenas 4 aprovadas, todas
  com confirmação obrigatória antes de executar
- Biometria processada localmente (zero tokens enviados para APIs externas)

## Resultado

Sistema em produção no laboratório. Métricas coletadas automaticamente
permitem calcular o tempo economizado vs. processo manual por categoria
de tarefa.

---

> Repositório privado — código disponível mediante solicitação.
> Desenvolvido por Pedro Augusto Gonçalves Leite · Grupo Odilon Santos · 2026
> 
