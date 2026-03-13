# Bot SENAI Editais

Bot para Telegram que monitora automaticamente os editais do [SENAI-PE](https://www.pe.senai.br/editais/), filtrando por cidade e área de TI. Quando um edital relevante é encontrado, o bot envia uma notificação com o link direto para o PDF.

---

## Funcionalidades

- Scraping automático da página de editais do SENAI-PE
- Filtro por cidade (padrão: Cabo de Santo Agostinho)
- Filtro por área de TI — verifica o título e, se necessário, o conteúdo do PDF
- Banco de dados local para evitar notificações duplicadas
- **Modo automático unificado** — monitor de disponibilidade + busca diária em um único comando
- **Monitor de disponibilidade** — detecta quando o site cai ou volta e notifica imediatamente
- **Busca imediata** — quando o site volta do offline, uma busca é disparada automaticamente
- Logs de progresso em tempo real durante a busca manual
- Histórico de disponibilidade (quando caiu, quando voltou, quanto tempo ficou offline)
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
| `/buscar` | Verifica novos editais agora (com logs em tempo real) |
| `/checar` | Verifica se o site de editais e o portal do aluno estão online |
| `/listar` | Exibe os editais aceitos |
| `/rejeitados` | Exibe editais rejeitados agrupados por motivo |
| `/status` | Painel completo: site, jobs ativos, histórico de disponibilidade |
| `/forcar` | Re-analisa editais rejeitados com a lista atual de termos de TI |
| `/auto` | Ativa o modo automático (monitor + busca diária) |
| `/parar` | Desativa o modo automático |
| `/ajuda` | Exibe a lista de comandos |

---

## Modo automático (`/auto`)

O `/auto` é o comando principal do bot. Ele sobe dois jobs internos que trabalham juntos sem conflito:

### Job monitor (a cada 5 minutos)
Faz um ping leve no site. Age apenas em **mudanças de estado**:

- **Site continua no mesmo estado** → silêncio total
- **Site caiu** → notifica imediatamente
- **Site voltou** → notifica imediatamente e **dispara uma busca completa de editais na hora**

### Job de busca (a cada 24 horas)
Faz a varredura completa de editais:

- **Site online** → executa normalmente e informa o resultado
- **Site offline** → ignora silenciosamente (o monitor já está cuidando disso e vai disparar a busca quando o site voltar)

### Por que não há conflito entre os jobs?

Os dois jobs compartilham o banco de dados local, mas têm responsabilidades distintas: o monitor só faz ping, o job de busca só faz scraping. O ponto de sincronização é único: o campo `site_online` no JSON. Isso garante que você nunca receba mensagens duplicadas nem spam de erros quando o site estiver fora do ar.

---

## Como funciona o filtro

Para cada edital encontrado na página, o bot aplica a seguinte lógica:

1. **Cidade** — o título deve conter o nome da cidade configurada. Se não, é ignorado.
2. **Título** — se o título já menciona um curso de TI, o edital é aceito diretamente.
3. **PDF** — se o título for genérico, o bot baixa o PDF e verifica o conteúdo. O edital é aceito se algum termo de TI for mencionado.

Editais já analisados (aceitos ou rejeitados) são salvos no banco local para não serem reprocessados. Use `/forcar` para re-analisar os rejeitados caso você adicione novos termos à lista `TI_TERMOS`.

---

## Configuração

As principais configurações ficam no topo do `app.py`:

```python
URL_EDITAIS       = "https://www.pe.senai.br/editais/"
URL_PORTAL        = "https://sge.pe.senai.br"
CIDADE_ALVO       = "cabo"
INTERVALO_MONITOR = 300       # segundos (5min) — ping de disponibilidade
INTERVALO_BUSCA   = 86_400    # segundos (24h)  — busca completa
MAX_RETRIES       = 3
```

Para monitorar outra cidade, basta alterar `CIDADE_ALVO`. Para adicionar termos de busca, edite a lista `TI_TERMOS`.

---

## Deploy (Render / Railway)

O servidor Flask sobe automaticamente na porta definida pela variável de ambiente `PORT`. As plataformas de hospedagem definem essa variável por padrão.

Rotas disponíveis:

- `GET /` — confirma que o serviço está no ar
- `GET /health` — retorna estatísticas detalhadas em JSON:
  ```json
  {
    "status": "ok",
    "site_online": false,
    "site_offline_desde": "2026-03-10T14:22:00",
    "aceitos": 3,
    "rejeitados": 47,
    "total_buscas": 12,
    "total_pdfs_baixados": 28,
    "ultima_busca_completa": "2026-03-10T14:00:00"
  }
  ```

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
