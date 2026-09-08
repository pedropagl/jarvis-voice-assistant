# 🤖 JARVIS — Assistente do Laboratório de Impressão 3D

![Python](https://img.shields.io/badge/python-3.13-blue?logo=python&logoColor=white)
![Status](https://img.shields.io/badge/status-em%20produção-brightgreen)
![Licença](https://img.shields.io/badge/uso-interno%20GOS-lightgrey)

Assistente de voz/chat com painel de produção ao vivo, integrado ao sistema
real da fábrica (**catos**) via MCP. Responde perguntas sobre máquinas,
tickets, peças e produção, reconhece quem está falando por biometria de voz,
e mostra tudo num painel de TV/PC em tempo real.

<!--
  📸 Print do painel aqui: tire um screenshot do dashboard (modo PC, com
  dados carregados) e salve como docs/screenshot-painel.png. Depois troque
  a linha abaixo por:
  ![Painel da JARVIS](docs/screenshot-painel.png)
-->

## 📋 O que o sistema faz

- **Conversa por voz ou texto** sobre máquinas, tickets, peças faltantes e
  produção pendente — com dados **reais** do catos quando disponível, ou
  dados simulados (`data/*.json`) como fallback.
- **Painel ao vivo** (`dashboard/`): radar de todas as máquinas, painel de
  eficiência da frota, tendência de produção, tickets e alertas — atualiza
  sozinho, sem precisar recarregar a página.
- **Biometria de voz**: identifica quem está falando, sem depender de senha.
- **Catálogo de conhecimento local** (`data/conhecimento.json`): responde
  perguntas comuns (matemática, história, geopolítica, robótica, impressão
  3D, etc.) sem gastar chamada de API.
- **Modo somente leitura**: a JARVIS nunca altera, apaga ou executa ações
  destrutivas no sistema real — só consulta.
- **Integrações opcionais** (todas desativadas por padrão até configuradas
  no `.env` — nada quebra sem elas):
  - Notificação push no navegador/celular quando uma máquina entra em alerta.
  - Alerta por WhatsApp.
  - Relatório quinzenal de produção (Google Sheets + e-mail).
  - PWA instalável (funciona como app no Android/iOS).

## 🗂️ Estrutura

```
jarvis_empresa/
├── main.py                    # Boot / modo texto e voz
├── core/                      # Cerebro: roteamento, LLM, MCP, voz, integracoes
├── company_system/            # Acesso aos dados da empresa (mock/fallback)
├── data/                      # Dados simulados + catalogo de conhecimento
├── dashboard/                 # Painel web (HTML/CSS/JS) + servidor local
├── credenciais/                # Chaves de integracoes (NAO versionado)
├── scripts/                   # Scripts auxiliares (ex.: gerar catalogo)
└── logs/                      # Registro de interacoes
```

## ⚙️ Como instalar

1. **Clonar e entrar na pasta do projeto:**
   ```powershell
   git clone https://github.com/pedropagl/jarvis-voice-assistant.git
   cd jarvis-voice-assistant
   ```

2. **Criar e ativar o ambiente virtual:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. **Instalar as dependências:**
   ```powershell
   pip install -r requirements.txt
   ```

4. **Configurar o ambiente:** copie `.env.example` para `.env` e preencha o
   que for usar (provedor de LLM, token do catos, integrações opcionais).
   ```powershell
   copy .env.example .env
   ```

## ▶️ Como usar

```powershell
jarvis --chat              # conversar digitando
jarvis --chat --voz        # digitando, com resposta em audio
jarvis --mic                # microfone ao vivo + biometria de voz
jarvis --cadastrar-voz      # cadastra a voz de uma pessoa do lab
jarvis painel               # abre o painel (dashboard) no navegador
jarvis mcp                  # testa a conexao com o catos
```

Para o painel abrir **automaticamente ao ligar o PC**, rode uma vez:
```powershell
instalar_autostart.bat
```
(reverte com `instalar_autostart.bat remover`)

## 🔒 Segurança

- Chaves e senhas ficam **só** no `.env` e em `credenciais/` — nunca no
  código, e ambos fora do controle de versão (`.gitignore`).
- A JARVIS só executa ferramentas de **leitura** no catos; ações que
  alterariam dados são bloqueadas por padrão (`core/mcp_client.py`).
- Dados de biometria de voz (`data/voiceprints.json`) nunca são versionados.
