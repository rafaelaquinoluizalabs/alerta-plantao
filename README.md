# Alerta de Plantão — Google Chat

Script Python que publica semanalmente, em um espaço do Google Chat, os plantonistas (SL, Cloud, OnPrem, Dados, TL) lidos da aba **HOJE** de uma planilha do Google Sheets. Inclui também os plantonistas de **sábado**, que pela regra de negócio são assumidos pela **semana seguinte**.

## Requisitos

- **Python 3.10+** (o `google-auth` mais recente não dá suporte oficial a versões anteriores).
- Conta no **Google Cloud** com permissão para criar Service Accounts.
- Acesso ao **Google Workspace** com permissão para criar Incoming Webhooks em espaços do Chat.

## Estrutura

```
alerta-plantao/
├── main.py
├── requirements.txt
├── .env.example
├── .env             # (não versionado)
└── credentials.json # (não versionado)
```

## Instalação

```bash
# 1. Clone o repositório
git clone https://github.com/rafaelaquinoluizalabs/alerta-plantao.git
cd alerta-plantao

# 2. Crie e ative o ambiente virtual (requer Python 3.10+)
python -m venv .venv
source .venv/bin/activate

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Configure as variáveis de ambiente
cp .env.example .env
# edite o .env e preencha GOOGLE_CHAT_WEBHOOK_URL

# 5. Coloque o credentials.json (Service Account) na raiz
#    Veja a seção "Configurar a Service Account" abaixo.
```

> ⚠️ **Nunca** versione `.env` ou `credentials.json` — ambos já estão no `.gitignore`.

## Uso

| Comando | O que faz |
|---|---|
| `python main.py` | Lê a planilha e **envia** a mensagem para o Google Chat. |
| `python main.py --dry-run` | Lê a planilha e **imprime no console** sem enviar. Útil para validar configuração. |
| `python main.py --today 2026-12-25` | Simula a execução para uma data arbitrária. Combine com `--dry-run` para QA. |
| `python main.py --help` | Lista todas as opções. |

Exemplo:

```bash
python main.py --dry-run --today 2026-05-22
```

### Agendamento semanal (cron)

Toda segunda às 09:00:

```cron
0 9 * * 1 cd /caminho/alerta-plantao && /caminho/.venv/bin/python main.py >> run.log 2>&1
```

---

## 1. Configurar a Service Account (Google Cloud)

1. Acesse <https://console.cloud.google.com/> e crie (ou selecione) um projeto.
2. Em **APIs & Services → Library**, habilite:
   - **Google Sheets API**
   - **Google Drive API**
3. Em **APIs & Services → Credentials → Create Credentials → Service Account**, crie uma conta de serviço (não é necessário atribuir roles do IAM).
4. Na conta criada, vá em **Keys → Add Key → Create new key → JSON**. Faça o download e salve como `credentials.json` na raiz do projeto (o caminho é configurável via `.env`).
5. Copie o e-mail da Service Account (algo como `nome@projeto.iam.gserviceaccount.com`).
6. Abra a planilha no Google Sheets e **compartilhe** com esse e-mail concedendo permissão de **Leitor**.

> Os escopos solicitados pelo script são `spreadsheets.readonly` e `drive.readonly` — apenas leitura.

## 2. Configurar o Incoming Webhook no Google Chat

1. Abra o **Espaço (Space)** no Google Chat onde a mensagem será publicada.
2. Clique no nome do espaço → **Apps e integrações** → **Webhooks** → **Adicionar webhook**.
3. Defina um nome (ex.: "Alerta Plantão") e, opcionalmente, um avatar.
4. Copie a URL gerada e coloque em `GOOGLE_CHAT_WEBHOOK_URL` no `.env`.

> Observação: webhooks só funcionam em espaços do Google Chat de um Workspace que permita esse recurso (políticas administrativas podem desabilitá-lo).

## 3. Variáveis do `.env`

| Variável | Obrigatória | Descrição |
|---|---|---|
| `GOOGLE_CHAT_WEBHOOK_URL` | Sim (não no `--dry-run`) | URL do Incoming Webhook. |
| `SPREADSHEET_ID` | Sim | ID da planilha (já preenchido no `.env.example`). |
| `CREDENTIALS_PATH` | Sim | Caminho para o JSON da Service Account. |
| `SHEET_TAB_NAME` | Não | Nome da aba. Default: `HOJE`. |

## 4. Regras de negócio e formato esperado da planilha

A aba **HOJE** é um calendário com a estrutura:

| Coluna | Conteúdo |
|---|---|
| **B** | Nome do mês (`MAIO`, `Maio/2026`, etc.). Pode estar **mesclada** verticalmente — só a primeira linha do bloco precisa conter o texto. |
| **C..I** | Dias do mês da semana (Dom..Sáb ou Seg..Dom, conforme a planilha). |
| **K** | Plantonista de **Cloud** |
| **L** | Plantonista de **OnPrem** |
| **M** | Plantonista de **Dados** |
| **P** | **Squad Lead (SL)** |
| **Q** | **TL** |

Os dados começam na **linha 4** (linha 3 é cabeçalho).

### Algoritmo

1. Determina o mês corrente pela data de hoje (`date.today()`, ou `--today`).
2. Percorre da linha 4 para baixo. Sempre que encontra um valor na coluna B, atualiza o "mês corrente" do scanner (`MAIO`, `JUNHO`, …). A comparação ignora acentos e capitalização.
3. Dentro do bloco do mês corrente, procura em C..I a célula cujo valor numérico seja o **dia de hoje**. Retorna essa linha.
4. Extrai **P, K, L, M, Q** dessa linha.
5. Extrai **K, L, M** da **linha seguinte** (plantão do sábado = próxima semana).

### Mensagem gerada

```
-------
💙 Plantão da Semana 💙

SL:  {squad_lead}
Cloud: {cloud}
Onprem: {onprem}
Dados:  {dados}
TL iPET:   {tl}

💙 Plantão do sábado:

Cloud:   {cloud_proxima_semana}
Onprem:  {onprem_proxima_semana}
Dados:  {dados_proxima_semana}
------
```

## 5. Troubleshooting

| Sintoma | Causa provável | Solução |
|---|---|---|
| `Variáveis de ambiente ausentes` | `.env` não criado/incompleto | `cp .env.example .env` e preencher. |
| `Arquivo de credenciais não encontrado em: ...` | `CREDENTIALS_PATH` aponta para arquivo inexistente | Verifique o caminho relativo (executa a partir do diretório do projeto). |
| `Planilha não encontrada` | Service Account não tem acesso | Compartilhar a planilha com o e-mail da SA como Leitor. |
| `Aba não encontrada` | Nome diferente de `HOJE` | Ajuste `SHEET_TAB_NAME` no `.env`. |
| `Não foi possível localizar o dia DD/MM` | Estrutura da planilha mudou ou mês não está em B | Conferir se a coluna B contém o nome do mês corrente. Rode `python main.py --dry-run --today YYYY-MM-DD` para debug. |
| `Google Chat retornou status 4xx` | Webhook inválido/revogado | Recriar o webhook no espaço do Chat e atualizar `.env`. |

Para debug detalhado da varredura de linhas, ative `DEBUG`:

```bash
PYTHONLOGLEVEL=DEBUG python main.py --dry-run    # (ou edite logging.basicConfig)
```
