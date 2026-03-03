# Bot SENAI Editais

Bot para Telegram que monitora automaticamente os editais do [SENAI-PE](https://www.pe.senai.br/editais/), filtrando por cidade e área de TI. Quando um edital relevante é encontrado, o bot envia uma notificação com o link direto para o PDF.

---

## Funcionalidades

- Scraping automático da página de editais do SENAI-PE
- Filtro por cidade (padrão: Cabo de Santo Agostinho)
- Filtro por área de TI — verifica o título e, se necessário, o conteúdo do PDF
- Banco de dados local para evitar notificações duplicadas
- Busca automática a cada 24 horas
- Endpoint HTTP para health check (compatível com Render e Railway)

---

## Requisitos

- Python 3.11+
- Token de bot do Telegram (obtido via [@BotFather](https://t.me/BotFather))

---

## Instalação

```bash
git clone https://github.com/seu-usuario/senai-bot.git
cd senai-bot

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

Crie o arquivo `.env` baseado no exemplo:

```bash
cp .env.example .env
```

Preencha as variáveis:

```env
BOT_TOKEN=seu_token_aqui
PORT=10000
```

---

## Uso

```bash
python app.py
```

### Comandos disponíveis no Telegram

| Comando | Descrição |
|---|---|
| `/buscar` | Verifica novos editais agora |
| `/listar` | Exibe os editais aceitos |
| `/status` | Mostra estatísticas do banco de dados |
| `/auto` | Ativa a busca automática a cada 24h |
| `/parar` | Desativa a busca automática |
| `/ajuda` | Exibe a lista de comandos |

---

## Como funciona o filtro

Para cada edital encontrado na página, o bot aplica a seguinte lógica:

1. **Cidade** — o título do edital deve conter o nome da cidade configurada. Se não, é ignorado.
2. **Título** — se o título já menciona um curso de TI, o edital é aceito diretamente.
3. **PDF** — se o título for genérico, o bot baixa o PDF e verifica o conteúdo. O edital é aceito se algum curso de TI for mencionado.

Editais já analisados (aceitos ou rejeitados) são salvos em `editais_cabo_ti.json` para não serem reprocessados.

---

## Configuração

As principais configurações ficam no topo do `app.py`:

```python
URL_EDITAIS    = "https://www.pe.senai.br/editais/"
CIDADE_ALVO    = "cabo"
INTERVALO_AUTO = 86_400   # segundos (24h)
MAX_RETRIES    = 3
```

Para monitorar outra cidade, basta alterar `CIDADE_ALVO`. Para adicionar termos de busca, edite a lista `TI_TERMOS`.

---

## Deploy (Render / Railway)

O servidor Flask sobe automaticamente na porta definida pela variável de ambiente `PORT`. As plataformas de hospedagem definem essa variável por padrão.

Rotas disponíveis:

- `GET /` — confirma que o serviço está no ar
- `GET /health` — retorna estatísticas do banco de dados em JSON

---

## Estrutura do projeto

```
senai-bot/
├── app.py                # Código principal
├── requirements.txt      # Dependências
├── .env.example          # Modelo de variáveis de ambiente
├── .env                  # Variáveis locais (não versionar)
├── editais_cabo_ti.json  # Banco de dados local (gerado em runtime)
└── bot.log               # Log de execução (gerado em runtime)
```

---

## Licença

MIT
