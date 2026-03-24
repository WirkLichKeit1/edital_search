# Bot SENAI Editais

> Monitor automatizado de editais do SENAI-PE para Telegram — filtra por cidade e área de interesse, analisa PDFs e notifica em tempo real.

---

## Sumário

- [Visão geral](#visão-geral)
- [Funcionalidades](#funcionalidades)
- [Pré-requisitos](#pré-requisitos)
- [Instalação](#instalação)
- [Configuração](#configuração)
  - [Variáveis de ambiente](#variáveis-de-ambiente)
  - [config.yaml](#configyaml)
- [Comandos](#comandos)
- [Arquitetura](#arquitetura)
  - [Estrutura de diretórios](#estrutura-de-diretórios)
  - [Fluxo de busca](#fluxo-de-busca)
  - [Modelo de dados](#modelo-de-dados)
- [Testes](#testes)
- [Deploy](#deploy)
- [Contribuindo](#contribuindo)
- [Licença](#licença)

---

## Visão geral

O **Bot SENAI Editais** é um serviço de monitoramento que acompanha a página de editais do SENAI-PE e notifica automaticamente via Telegram quando surgem oportunidades relevantes para o usuário. A filtragem acontece em duas etapas: primeiro por cidade (no título do edital) e depois por termos de interesse (no título e, se necessário, no conteúdo completo do PDF).

Cada usuário configura sua própria cidade e lista de termos de interesse. O bot persiste o histórico de editais aceitos e rejeitados por usuário, evitando notificações duplicadas.

---

## Funcionalidades

| Recurso | Detalhe |
|---|---|
| **Scraping de editais** | Coleta todos os links PDF da página de editais do SENAI-PE |
| **Filtro por cidade** | Descarta editais cujo título não contenha a cidade configurada |
| **Filtro por termos** | Busca termos de interesse no título e, se necessário, no texto completo do PDF |
| **Análise de PDF** | Download e extração de texto de PDFs para editais com título genérico |
| **Progresso em tempo real** | Atualizações ao vivo no Telegram durante a busca |
| **Monitoramento de disponibilidade** | Detecta quando o site cai ou volta e notifica o usuário |
| **Modo automático** | Job de monitor (ping periódico) + job de busca (varredura agendada) |
| **Re-análise de rejeitados** | Reavalia editais rejeitados com a lista de termos atualizada |
| **Configuração por usuário** | Cidade, termos e intervalos individuais por chat_id |
| **Persistência JSON** | Banco de dados leve em `data/db.json`, sem dependências externas |
| **Health check HTTP** | Servidor Flask integrado para plataformas de hospedagem |

---

## Pré-requisitos

- **Python** 3.11 ou superior
- **Token de bot** do Telegram — obtenha via [@BotFather](https://t.me/BotFather)
- Acesso à internet para scraping e envio de mensagens

---

## Instalação

```bash
# 1. Clone o repositório
git clone https://github.com/seu-usuario/senai-editais-bot.git
cd senai-editais-bot

# 2. (Recomendado) Crie e ative um ambiente virtual
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
.venv\Scripts\activate         # Windows

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Configure as variáveis de ambiente
cp .env.example .env
# Abra o .env e preencha pelo menos BOT_TOKEN

# 5. (Opcional) Ajuste o config.yaml com a cidade e os termos padrão

# 6. Inicie o bot
python main.py
```

O diretório `data/` e o arquivo `db.json` são criados automaticamente na primeira execução.

---

## Configuração

A configuração segue uma hierarquia de prioridade:

```
Variáveis de ambiente / .env  (maior prioridade)
        ↓
    config.yaml
        ↓
  Valores padrão internos
```

### Variáveis de ambiente

Crie um arquivo `.env` na raiz do projeto com as seguintes variáveis:

| Variável | Obrigatória | Padrão | Descrição |
|---|---|---|---|
| `BOT_TOKEN` | **sim** | — | Token do bot fornecido pelo BotFather |
| `URL_EDITAIS` | não | `https://www.pe.senai.br/editais/` | URL da página de editais do SENAI-PE |
| `URL_PORTAL` | não | `https://sge.pe.senai.br` | URL do portal do aluno (usado no `/checar`) |
| `CIDADE_PADRAO` | não | `cabo` | Cidade padrão aplicada a novos usuários |
| `INTERVALO_MONITOR` | não | `300` | Frequência do ping de disponibilidade (segundos) |
| `INTERVALO_BUSCA` | não | `86400` | Frequência da busca completa (segundos) |
| `PORT` | não | `10000` | Porta do servidor Flask para health check |
| `REQUEST_TIMEOUT` | não | `30` | Timeout máximo por requisição HTTP (segundos) |
| `MAX_RETRIES` | não | `3` | Número de tentativas antes de desistir de um request |

Exemplo de `.env` mínimo:

```env
BOT_TOKEN=123456789:ABCdef...
```

### config.yaml

Define os valores padrão aplicados a **novos usuários**. Usuários já cadastrados mantêm sua configuração individual, que pode ser alterada pelos comandos `/config`, `/addtermo` e `/rmtermo`.

```yaml
# Cidade filtrada nos títulos dos editais
cidade_padrao: cabo

# Intervalos de tempo (em segundos)
intervalo_monitor: 300      # 5 min  — ping leve de disponibilidade
intervalo_busca: 86400      # 24 h   — varredura completa de editais

# URLs monitoradas
url_editais: "https://www.pe.senai.br/editais/"
url_portal:  "https://sge.pe.senai.br"

# Termos usados para identificar editais de TI
termos_padrao:
  - desenvolvimento de sistemas
  - informatica
  - redes de computadores
  - programacao
  - banco de dados
  - tecnologia da informacao
  - ti
  # adicione ou remova termos conforme necessário
```

> **Nota:** a normalização de acentos é feita automaticamente. `"informática"` e `"informatica"` são equivalentes na comparação.

---

## Comandos

### Busca e listagem

| Comando | Descrição |
|---|---|
| `/buscar` | Executa uma busca completa agora, com log de progresso em tempo real |
| `/listar` | Exibe os últimos editais aceitos (até 20) |
| `/rejeitados` | Lista editais rejeitados agrupados por motivo de rejeição |
| `/forcar` | Re-analisa editais rejeitados por conteúdo usando os termos atuais |

### Monitoramento

| Comando | Descrição |
|---|---|
| `/checar` | Verifica o status do site de editais e do portal do aluno |
| `/auto` | Ativa o monitoramento automático (monitor periódico + busca agendada) |
| `/parar` | Desativa todos os jobs automáticos do usuário |

### Configuração

| Comando | Exemplo | Descrição |
|---|---|---|
| `/config` | `/config` | Exibe a configuração atual do usuário |
| `/config cidade` | `/config cidade recife` | Altera a cidade do filtro |
| `/addtermo` | `/addtermo machine learning` | Adiciona um termo à lista de interesse |
| `/rmtermo` | `/rmtermo ads` | Remove um termo da lista |
| `/termos` | `/termos` | Lista todos os termos ativos |
| `/resetconfig` | `/resetconfig` | Restaura cidade e termos para os valores padrão |

### Informações

| Comando | Descrição |
|---|---|
| `/status` | Painel completo: site, modo automático, estatísticas e histórico |
| `/ajuda` | Exibe a mensagem de ajuda com todos os comandos |

---

## Arquitetura

### Estrutura de diretórios

```
senai-editais-bot/
│
├── main.py               # Ponto de entrada: inicializa Flask, registra handlers, inicia polling
├── config.py             # Carrega e valida Settings a partir de .env e config.yaml
├── server.py             # Servidor Flask para health check (GET / e GET /health)
├── config.yaml           # Padrões globais (cidade, termos, intervalos, URLs)
├── requirements.txt      # Dependências do projeto
│
├── bot/
│   ├── commands/
│   │   ├── info.py       # /start, /ajuda, /status
│   │   ├── monitor.py    # /checar, /auto, /parar
│   │   ├── busca.py      # /buscar, /listar, /rejeitados, /forcar
│   │   └── config.py     # /config, /addtermo, /rmtermo, /termos, /resetconfig
│   │
│   ├── database.py       # Dataclasses (Edital, UserData, etc.) + persistência JSON
│   ├── scraper.py        # HTTP com retry, scraping HTML, extração de texto de PDF
│   ├── filters.py        # Funções puras de filtragem por cidade e termos
│   ├── formatters.py     # Formatação MarkdownV2 e helpers de envio
│   └── jobs.py           # Jobs assíncronos: job_monitor e job_busca
│
├── tests/
│   ├── conftest.py
│   ├── test_database.py  # CRUD, serialização, disponibilidade
│   ├── test_filters.py   # Normalização, filtros de cidade e termos
│   └── test_scraper.py   # Scraping, busca e re-análise (rede mockada)
│
└── data/
    └── db.json           # Banco de dados (criado automaticamente)
```

### Fluxo de busca

A cada `/buscar` ou disparo do `job_busca`, o seguinte pipeline é executado:

```
pegar_editais(url)
    └── para cada edital na página
            │
            ├── já conhecido? (link em aceitos ou rejeitados)
            │       └── sim → pula (ja_conhecidos++)
            │
            ├── 1. Filtro de cidade
            │       └── título não contém a cidade? → rejeita (motivo: cidade)
            │
            ├── 2. Filtro por título
            │       └── algum termo encontrado no título? → aceita (encontrado_em: "titulo")
            │
            └── 3. Análise do PDF
                    ├── extrair_texto_pdf(link)
                    ├── algum termo no texto? → aceita (encontrado_em: "pdf")
                    └── nenhum termo → rejeita (motivo: "sem termos de TI")
```

O modo automático adiciona dois jobs independentes por usuário:

- **`job_monitor`** — ping leve a cada `intervalo_monitor` segundos. Notifica quando o estado muda (online ↔ offline). Ao detectar que o site voltou, dispara uma busca imediata.
- **`job_busca`** — varredura completa a cada `intervalo_busca` segundos. Executa silenciosamente se o site estiver online; ignora e registra log se estiver offline (a recuperação é coberta pelo `job_monitor`).

### Modelo de dados

O banco é um único arquivo `data/db.json` particionado por `chat_id`. Cada usuário tem a seguinte estrutura:

```
UserData
├── config: UserConfig
│   ├── cidade: str
│   ├── termos: list[str]
│   ├── intervalo_monitor: int
│   └── intervalo_busca: int
├── aceitos: list[Edital]
├── rejeitados: list[Edital]
├── stats: UserStats
│   ├── total_buscas: int
│   └── total_pdfs_baixados: int
├── site_online: bool | None
├── site_offline_desde: str | None      # ISO datetime
├── ultima_busca_completa: str | None   # ISO datetime
└── historico_disponibilidade: list[EventoDisponibilidade]
```

```
Edital
├── titulo: str
├── link: str
├── aceito_em / rejeitado_em: str       # ISO datetime
├── encontrado_em: str                  # "titulo" | "pdf" | "reanalise_titulo"
├── termos_ti: list[str]                # termos que levaram à aceitação
└── motivo: str | None                  # motivo de rejeição
```

---

## Testes

A suíte cobre as três camadas principais do projeto sem depender de rede ou de um bot Telegram real.

```bash
# Rodar todos os testes
pytest

# Com output detalhado
pytest -v

# Apenas uma suíte específica
pytest tests/test_filters.py
```

| Arquivo | Escopo | Estratégia |
|---|---|---|
| `test_filters.py` | Normalização e filtros de cidade/termos | Funções puras, sem mocks |
| `test_database.py` | CRUD de editais, config, disponibilidade | `tmp_path` do pytest para banco isolado |
| `test_scraper.py` | Scraping HTML, extração de PDF, busca completa | `AsyncMock` para isolar toda I/O de rede |

---

## Deploy

O bot foi projetado para rodar em plataformas PaaS como **Render** ou **Railway**. Um servidor Flask sobe em thread daemon paralela ao bot para satisfazer o health check da plataforma.

### Endpoints HTTP

| Endpoint | Método | Resposta |
|---|---|---|
| `/` | `GET` | `{"status": "ok", "bot": "SENAI Editais"}` |
| `/health` | `GET` | Métricas agregadas de todos os usuários |

Exemplo de resposta do `/health`:

```json
{
  "status": "ok",
  "total_usuarios": 3,
  "total_aceitos": 12,
  "total_rejeitados": 87,
  "total_buscas": 45,
  "total_pdfs": 30
}
```

### Render

1. Crie um novo serviço **Web Service** apontando para este repositório
2. Defina o **Start Command** como `python main.py`
3. Adicione a variável de ambiente `BOT_TOKEN` no painel do serviço
4. Configure o health check para `GET /health`

### Considerações sobre persistência

> **Atenção:** plataformas com sistema de arquivos efêmero (Render free tier, Railway) resetam o `data/db.json` a cada redeploy. Para persistência entre deploys, monte um volume persistente ou migre a camada de armazenamento para um banco externo (SQLite via volume, Redis, PostgreSQL, etc.).

---

## Contribuindo

Contribuições são bem-vindas. Siga o fluxo padrão:

1. Fork do repositório
2. Crie uma branch descritiva: `git checkout -b feat/nova-funcionalidade`
3. Implemente as alterações com testes cobrindo o novo comportamento
4. Certifique-se de que toda a suíte passa: `pytest`
5. Abra um Pull Request com descrição clara do que foi alterado e por quê

---

## Licença

Distribuído sob a licença **MIT**. Consulte o arquivo `LICENSE` para mais detalhes.