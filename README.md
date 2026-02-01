# 🤖 SENAI Edital Monitor Bot

Bot para Telegram integrado com Flask que monitora automaticamente o site de editais do SENAI-PE, filtrando oportunidades para a cidade do **Cabo de Santo Agostinho** na área de **TI**.

## 📋 Sobre o Projeto

O script realiza uma varredura (web scraping) no site oficial do SENAI-PE, analisa os títulos dos editais e, caso necessário, "lê" o conteúdo interno dos PDFs para identificar cursos de tecnologia, notificando o utilizador via Telegram.

---

## ⚙️ Explicação das Funções

O código está dividido em blocos lógicos que funcionam como uma esteira de processamento:

### 1. Coleta e Filtragem Inicial

* **`pegar_editais()`**: Acessa a URL do SENAI e extrai todos os links `<a>` que apontam para arquivos PDF e começam com a palavra "Edital".
* **`edital_eh_cabo(titulo)`**: Normaliza o título (remove acentos e espaços) e verifica se a palavra "cabo" está presente, garantindo que o edital é da localidade correta.
* **`titulo_indica_ti(titulo)`**: Verifica se o próprio nome do arquivo já menciona termos como "Desenvolvimento", "Redes" ou "Informática", agilizando o processo.

### 2. Inspeção Profunda (Análise de PDF)

* **`baixar_pdf(url_pdf)`**: Descarrega o ficheiro PDF para uma pasta temporária para que o script possa analisar o seu conteúdo.
* **`extrair_texto_pdf(arquivo)`**: Utiliza a biblioteca `pypdf` para converter o conteúdo visual do PDF em texto pesquisável.
* **`pdf_contem_ti(texto_pdf)`**: Varre o texto extraído em busca de qualquer termo da lista de TI, permitindo encontrar vagas mesmo quando o título do edital é genérico.

### 3. Lógica de Negócio e Persistência

* **`buscar_novos_editais()`**: É a função central. Ela coordena os filtros, ignora editais já processados (consultando o ficheiro `editais_cabo_ti.json`) e atualiza a base de dados local com as novas descobertas.

### 4. Interface do Telegram

* **`start()`**: Comando inicial que apresenta as opções ao utilizador.
* **`buscar()`**: Aciona manualmente a varredura do site e reporta os resultados encontrados no momento.
* **`auto()` e `job_diario()**`: Configuram uma rotina agendada (Job Queue) para que o bot trabalhe sozinho a cada 24 horas, notificando apenas se houver novidades.

### 5. Infraestrutura

* **`rodar_flask()`**: Mantém um servidor HTTP ativo. Isto é essencial para evitar que plataformas de hospedagem (como o Render) desliguem o bot por inatividade.
* **`main()`**: Utiliza *threading* para correr o servidor Flask e o Bot do Telegram simultaneamente.

---

## 🛠️ Instalação e Uso

1. **Instale as dependências:**
```bash
pip install flask pypdf unidecode beautifulsoup4 requests python-telegram-bot python-dotenv

```


2. **Configure o ficheiro `.env`:**
```env
BOT_TOKEN=seu_token_aqui
PORT=10000

```


3. **Inicie o bot:**
```bash
python nome_do_arquivo.py

```



---

## 🤖 Comandos Disponíveis

* `/start` - Inicia o bot.
* `/buscar` - Procura editais manualmente.
* `/auto` - Ativa a verificação automática diária.
