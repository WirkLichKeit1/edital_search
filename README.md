# Bot SENAI Editais

Bot para Telegram que monitora a página de editais do SENAI-PE e notifica quando surgem novos processos seletivos na sua cidade e área de interesse.

## Funcionalidades

- **Busca inteligente** — scraping da página de editais com filtro por cidade e termos de TI; analisa o título do link, o texto ao redor na página e o conteúdo do PDF
- **Monitoramento automático** — verifica periodicamente se o site está online e dispara busca ao detectar retorno após queda
- **Debounce de falhas** — só declara o site offline após 3 falhas consecutivas, evitando falsos alertas por instabilidades passageiras
- **Horário fixo de busca** — configure um horário diário exato para a varredura (ex: todo dia às 08:00), ou use o modo por intervalo
- **Configuração por usuário** — cada usuário tem sua própria cidade, lista de termos, intervalo e horário de busca
- **Re-análise de rejeitados** — após adicionar novos termos, `/forcar` reanálisa editais já descartados
- **Persistência entre restarts** — modo automático e horário são restaurados automaticamente quando o bot reinicia

## Requisitos

- Python 3.11+
- Token de bot do Telegram (via [@BotFather](https://t.me/BotFather))

## Instalação

```bash
# Clone o repositório
git clone https://github.com/WirkLichKeit1/edital_search.git
cd edital_search

# Instale as dependências
pip install -r requirements.txt

# Configure as variáveis de ambiente
cp .env.example .env
# Edite .env e preencha BOT_TOKEN
```

## Configuração

### `.env`

```env
BOT_TOKEN=seu_token_aqui

# Opcional — sobrescrevem os valores de config.yaml
URL_EDITAIS=https://www.pe.senai.br/editais/
URL_PORTAL=https://sge.pe.senai.br
CIDADE_PADRAO=cabo
INTERVALO_MONITOR=300
INTERVALO_BUSCA=86400

# Opcional — não têm equivalente em config.yaml, usam o default se omitidos
PORT=10000
REQUEST_TIMEOUT=30
MAX_RETRIES=3
```

### `config.yaml`

Define os padrões para novos usuários. Cada usuário pode sobrescrever via comandos do Telegram.

```yaml
cidade_padrao: cabo
intervalo_monitor: 300    # segundos (5 min)
intervalo_busca: 86400    # segundos (24 h)

url_editais: "https://www.pe.senai.br/editais/"
url_portal:  "https://sge.pe.senai.br"

termos_padrao:
  - informatica
  - desenvolvimento de sistemas
  - redes de computadores
  - ti
  # ...
```

## Execução

```bash
python main.py
```

O bot inicia o polling do Telegram e um servidor Flask na porta configurada (padrão `10000`) para health check.

## Comandos do Telegram

### Busca e listagem

| Comando | Descrição |
|---|---|
| `/buscar` | Busca editais agora com progresso em tempo real |
| `/listar` | Exibe os editais aceitos |
| `/rejeitados` | Exibe editais rejeitados agrupados por motivo |
| `/forcar` | Re-analisa rejeitados com a lista de termos atual |

### Monitoramento

| Comando | Descrição |
|---|---|
| `/checar` | Verifica se o site está online agora |
| `/auto` | Ativa o modo automático (monitor + busca periódica) |
| `/parar` | Desativa o modo automático |
| `/horario` | Exibe o horário atual de busca |
| `/horario HH:MM` | Define um horário fixo diário para a busca (ex: `08:00`) |
| `/horario off` | Remove o horário fixo, volta ao modo por intervalo |

### Configuração

| Comando | Descrição |
|---|---|
| `/config` | Exibe a configuração atual |
| `/config cidade <nome>` | Altera a cidade filtrada |
| `/addtermo <termo>` | Adiciona um termo de busca |
| `/rmtermo <termo>` | Remove um termo de busca |
| `/termos` | Lista os termos ativos |
| `/resetconfig` | Restaura cidade, termos e horário para os padrões |
| `/status` | Painel completo com estado do site, estatísticas e histórico |

## Pipeline de filtragem

Cada edital encontrado na página passa por três etapas em ordem:

```
1. Filtro de cidade   → título do link OU texto ao redor na página
       ↓ passou
2. Filtro por termos  → título do link OU texto ao redor na página
       ↓ não encontrou termos na página
3. Análise do PDF     → baixa e extrai o texto completo do arquivo
```

O edital é aceito assim que passa em qualquer etapa; o PDF só é baixado se as etapas anteriores não forem conclusivas, economizando banda e tempo.

## Estrutura do projeto

```
.
├── main.py                  # Ponto de entrada
├── config.py                # Carrega settings do .env e config.yaml
├── config.yaml              # Padrões globais do bot
├── server.py                # Servidor Flask para health check
├── requirements.txt
├── bot/
│   ├── database.py          # Modelos de dados e persistência (JSON)
│   ├── scraper.py           # Scraping da página e extração de PDFs
│   ├── filters.py           # Funções puras de filtragem (cidade, termos)
│   ├── formatters.py        # Formatação de mensagens Telegram (MarkdownV2)
│   ├── jobs.py              # Jobs automáticos (monitor e busca periódica)
│   ├── scheduler.py         # Helper central de agendamento de jobs
│   └── commands/
│       ├── info.py          # /start, /ajuda, /status
│       ├── monitor.py       # /checar, /auto, /parar
│       ├── busca.py         # /buscar, /listar, /rejeitados, /forcar
│       └── config.py        # /config, /addtermo, /rmtermo, /termos, /resetconfig, /horario
└── tests/                   # Suíte pytest (filtros, banco de dados, scraper)
```

## Persistência

Os dados de todos os usuários são armazenados em `data/db.json`. O arquivo é escrito atomicamente (via arquivo temporário + rename) para evitar corrupção em caso de interrupção abrupta.

Cada usuário armazena:
- Configuração individual (cidade, termos, intervalos, horário de busca)
- Lista de editais aceitos e rejeitados (com motivo)
- Estado de disponibilidade do site e histórico de quedas
- Estatísticas de uso (buscas realizadas, PDFs analisados)

## Testes

```bash
pytest
```

Os testes cobrem as funções de filtragem, operações de banco e o scraper completo (com I/O de rede totalmente mockado).

## Health check

O servidor Flask expõe dois endpoints:

- `GET /` — `{"status": "ok", "bot": "SENAI Editais"}`
- `GET /health` — métricas agregadas de todos os usuários

```json
{
  "status": "ok",
  "total_usuarios": 3,
  "total_aceitos": 12,
  "total_rejeitados": 47,
  "total_buscas": 30,
  "total_pdfs": 18
}
```

## Deploy (Render / Railway)

O bot foi projetado para rodar em plataformas PaaS com processo único. Configure a variável `BOT_TOKEN` no painel da plataforma e aponte o health check para `/health` na porta definida em `PORT`.
