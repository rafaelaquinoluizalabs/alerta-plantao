# Script de Ajuste Automático de Plantonistas VictorOps (Splunk)

Este script sincroniza automaticamente os plantonistas do VictorOps (Splunk On-Call) com a escala definida em uma planilha Google Sheets, garantindo que a planilha seja sempre a fonte da verdade.

## Pré-requisitos

- Python 3.8+
- Pacotes Python: `gspread`, `google-auth`, `requests`, `python-dotenv`, `pandas`, `openpyxl`
- Credenciais de Service Account do Google (arquivo JSON)
- Permissões de leitura na planilha Google Sheets
- Acesso à API do VictorOps (Splunk On-Call) com API ID, API Key e Org ID

## Configuração

1. **Variáveis de Ambiente**

Crie um arquivo `.env` na raiz do projeto com as seguintes variáveis:

```
SPREADSHEET_ID=<ID da sua planilha Google>
CREDENTIALS_PATH=<caminho para o arquivo de credenciais do Google>
VICTOROPS_API_ID=<sua API ID do VictorOps>
VICTOROPS_API_KEY=<sua API Key do VictorOps>
VICTOROPS_ORG_ID=<seu Org ID do VictorOps>
```

2. **Mapeamento de Competências**

O mapeamento entre competências, abas e colunas da planilha está em `tools/planilha_map.py`. Ajuste se necessário.

3. **Instalação de Dependências**

No terminal, execute:

```
pip install -r requirements.txt
gspread google-auth requests python-dotenv pandas openpyxl
```

## Execução

Entre no diretório do projeto e ative seu ambiente virtual, se houver:

```
source .venv/bin/activate
```

### Modo Simulação (Dry-Run)

Para simular o ajuste sem alterar o VictorOps (Splunk):

```
python tools/ajustar_plantonistas.py --ajustar --dry-run
```

### Modo Real (Ajuste de fato)

Para sincronizar de verdade o VictorOps com a planilha:

```
python tools/ajustar_plantonistas.py --ajustar
```

O script irá:
- Ler a planilha Google Sheets e identificar o plantonista da semana para cada competência.
- Consultar o VictorOps e comparar o plantonista atual.
- Se houver divergência, atualizar o VictorOps para refletir a planilha (exceto em dry-run).
- Exibir no terminal o status de cada ajuste.

## Observações

- A planilha **não é alterada** pelo script. Ela é sempre a referência.
- O ajuste é feito apenas se houver diferença entre o VictorOps e a planilha.
- Use sempre o modo `--dry-run` para testar antes de rodar em produção.

## Suporte

Em caso de dúvidas, consulte o código-fonte ou entre em contato com o responsável pelo projeto.
