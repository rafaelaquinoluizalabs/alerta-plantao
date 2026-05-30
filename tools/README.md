# `build_mentions.py` — Gerador de `mentions.json`

Resolve **e-mails → USER_IDs** do Google Workspace (via Admin SDK Directory API)
e gera o `mentions.json` usado pelo `main.py` para mencionar pessoas no Google Chat.

## Pré-requisitos

1. **Service Account com domain-wide delegation** habilitada.
2. No **Admin Console** → *Security → API Controls → Domain-wide Delegation*,
   autorizar o **Client ID** da Service Account com o escopo:
   ```
   https://www.googleapis.com/auth/admin.directory.user.readonly
   ```
3. No `.env`, definir:
   ```env
   CREDENTIALS_PATH=./credentials.json
   ADMIN_EMAIL=admin@suaempresa.com   # admin do Workspace a ser impersonado
   MENTIONS_PATH=./mentions.json      # opcional (caminho de saída padrão)
   ```

## Uso

```bash
# Gera/atualiza o mentions.json (caminho de MENTIONS_PATH ou ./mentions.json)
python tools/build_mentions.py --csv equipe.csv

# Apenas imprime o resultado no console, sem gravar arquivo
python tools/build_mentions.py --csv equipe.csv --dry-run

# Grava em um caminho específico
python tools/build_mentions.py --csv equipe.csv --output config/mentions.json
```

| Opção | Descrição |
|---|---|
| `--csv` | **Obrigatório.** Caminho do CSV de entrada. |
| `--output` | Caminho de saída. Default: `MENTIONS_PATH` ou `./mentions.json`. |
| `--dry-run` | Resolve e imprime o JSON; não grava arquivo. |

## Formato do arquivo `.csv`

O CSV precisa de um cabeçalho com **duas colunas**: nome e e-mail.

- **Coluna do nome** — aceita os títulos: `Nome`, `Name` ou `Plantonista`.
  O valor vira a **chave** do `mentions.json` e deve bater com o nome
  **exatamente como aparece na planilha**.
- **Coluna do e-mail** — aceita os títulos: `Email`, `E-mail` ou `Mail`.
  Usado apenas para resolver o ID.

Detalhes:
- A ordem das colunas não importa.
- Cabeçalho é case-insensitive e tolera BOM (arquivos salvos do Excel).
- Linhas sem nome ou sem e-mail são ignoradas (com aviso no log).

### Exemplo mínimo

```csv
Nome,Email
João Silva,joao.silva@suaempresa.com
Maria Souza,maria.souza@suaempresa.com
```

### Exemplo com colunas extras e ordem trocada

Colunas adicionais são ignoradas; só `Nome` e `Email` são usados:

```csv
Email,Squad,Nome
joao.silva@suaempresa.com,Cloud,João Silva
maria.souza@suaempresa.com,Dados,Maria Souza
```

### Resultado gerado (`mentions.json`)

```json
{
  "João Silva": "123456789012345678901",
  "Maria Souza": "987654321098765432109"
}
```

## Descobrir o seu próprio ID

```bash
echo "Nome,Email
Seu Nome,seu.email@suaempresa.com" > eu.csv
python tools/build_mentions.py --csv eu.csv --dry-run
```

## Códigos de saída

| Código | Significado |
|---|---|
| `0` | Sucesso (arquivo gravado ou impresso em `--dry-run`). |
| `1` | Erro: `ADMIN_EMAIL` ausente, CSV inválido, falha de delegation/auth, rede, ou nenhum ID resolvido. |

## Erros comuns

| Mensagem | Causa | Solução |
|---|---|---|
| `ADMIN_EMAIL ausente` | Variável não definida | Preencher `ADMIN_EMAIL` no `.env`. |
| `Falha de autenticação/delegation` | Client ID não autorizado ou escopo incorreto | Conferir a delegation no Admin Console e o escopo `admin.directory.user.readonly`. |
| `Usuário não encontrado no diretório` | E-mail incorreto ou fora do domínio | Verificar o e-mail no CSV. |
| `Nenhuma linha válida encontrada no CSV` | Cabeçalho sem colunas de nome/e-mail | Usar títulos aceitos (`Nome`, `Email`). |
