# JARVIS — Assistente de Produção (Fase 1: Arquitetura)

Assistente de voz para manufatura / impressão 3D. **Modo somente leitura** nesta versão:
a JARVIS apenas consulta dados, nunca altera, apaga ou modifica produção. Os dados são
**simulados** em arquivos JSON na pasta `data/`.

> Esta entrega cobre **apenas a Fase 1**: estrutura, arquivos base e boot. Sem voz, sem
> Gemini, sem consulta avançada e sem interface ainda.

## Estrutura
```
jarvis_empresa/
├── main.py                 # Boot: carrega dados e confirma que o projeto inicia
├── requirements.txt        # Dependencias (Fase 1: apenas python-dotenv)
├── .env.example            # Modelo de variaveis de ambiente (copiar para .env)
├── .gitignore
├── core/                   # Cerebro da assistente (voz, Gemini, roteamento, seguranca, logs)
├── company_system/         # Acesso aos dados da empresa (maquinas, pecas, tickets, regras)
├── data/                   # Bases simuladas em JSON (somente leitura)
├── dashboard/              # Painel da TV (placeholders; construido na Fase 11)
└── logs/                   # Registro de interacoes (interactions.log)
```

## Como rodar no Windows (PowerShell)

1. **Criar a pasta do projeto** e colocar os arquivos dentro dela (ou descompactar o zip).
   ```powershell
   cd C:\Projetos
   cd jarvis_empresa
   ```

2. **Criar e ativar o ambiente virtual:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
   > Se o PowerShell bloquear a ativação, rode uma vez:
   > `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
   > (No Prompt de Comando, a ativação é `.\.venv\Scripts\activate.bat`.)

3. **Instalar as dependências:**
   ```powershell
   pip install -r requirements.txt
   ```

4. **Configurar o ambiente:** copie `.env.example` para `.env`.
   ```powershell
   copy .env.example .env
   ```
   Na Fase 1 não é preciso preencher a `GEMINI_API_KEY` (ela só será usada na Fase 7).

5. **Rodar o projeto:**
   ```powershell
   python main.py
   ```
   Você deve ver o banner da JARVIS, o resumo do boot (5 máquinas, 5 peças, 3 tickets,
   modo somente leitura) e a confirmação de inicialização. O mesmo registro aparece em
   `logs/interactions.log`.
