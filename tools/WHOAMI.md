# Serviço web whoami.py — Descubra seu USER_ID do Google Chat

Este serviço permite que qualquer pessoa descubra seu próprio USER_ID do Google Chat (o número usado para menções reais `<users/USER_ID>`) de forma simples, bastando fazer login com a conta Google.

Além de exibir o nome e o USER_ID na tela, cada acesso autenticado é registrado/incrementado no arquivo `mentions-web.json` (nome → USER_ID), pronto para uso no alerta de plantão.

---

## 1. Pré-requisitos

- Python 3.9+ (recomendado 3.10+)
- Dependências instaladas:
  ```bash
  pip install -r requirements.txt
  ```
- Um projeto no Google Cloud com OAuth 2.0 Client ID do tipo "Web application"
- Configuração do consent screen (pode ser "Internal" para uso só no domínio)

---

## 2. Configuração do .env

No arquivo `.env` (ou exportando variáveis), defina:

```
GOOGLE_OAUTH_CLIENT_ID=xxxx.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=xxxx
OAUTH_REDIRECT_URI=http://localhost:8080/oauth2/callback
MENTIONS_WEB_PATH=./mentions-web.json
FLASK_SECRET_KEY=uma-string-aleatoria
```

- `GOOGLE_OAUTH_CLIENT_ID` e `GOOGLE_OAUTH_CLIENT_SECRET`: obtenha no Google Cloud Console → APIs & Services → Credentials → Create credentials → OAuth client ID → Web application.
- `OAUTH_REDIRECT_URI`: deve ser exatamente igual ao cadastrado no Console (padrão: `http://localhost:8080/oauth2/callback`).
- `MENTIONS_WEB_PATH`: onde será salvo o arquivo incremental (padrão: `mentions-web.json`).
- `FLASK_SECRET_KEY`: qualquer string aleatória para proteger a sessão.

---

## 3. Como rodar

```bash
pip install -r requirements.txt
python tools/whoami.py
```

O serviço sobe em `http://localhost:8080/`.

---

## 4. Como usar

1. Acesse `http://localhost:8080/` no navegador.
2. Clique em **Entrar com Google**.
3. Faça login com sua conta corporativa.
4. O site exibirá:
   - **Nome**
   - **E-mail**
   - **USER_ID do Google Chat** (com botão Copiar)
5. O acesso é registrado/incrementado em `mentions-web.json`.
6. Envie o USER_ID para quem for responsável pelo alerta, ou use o arquivo gerado diretamente no `main.py`:
   ```env
   MENTIONS_PATH=./mentions-web.json
   ```

---

## 5. Como obter as credenciais OAuth

1. Acesse o [Google Cloud Console](https://console.cloud.google.com/)
2. APIs & Services → OAuth consent screen: configure o app (Internal ou External)
3. APIs & Services → Credentials → Create credentials → OAuth client ID → Web application
4. Em "Authorized redirect URIs", adicione:
   - `http://localhost:8080/oauth2/callback`
5. Copie o Client ID e o Client secret para o `.env`

---

## 6. Segurança

- O serviço usa parâmetro `state` para proteção anti-CSRF.
- O `id_token` é validado com a audiência do seu Client ID.
- O arquivo `mentions-web.json` é protegido por lock e gravação atômica.
- O arquivo já está no `.gitignore`.

---

## 7. Observações

- O USER_ID exibido é o mesmo usado nas menções reais do Google Chat (`<users/USER_ID>`).
- O arquivo `mentions-web.json` pode ser usado diretamente no `main.py` ou mesclado com outros arquivos de menções.
- O serviço não precisa de domínio público nem HTTPS para uso interno/testes.
- Para uso externo, basta expor a porta 8080 (ngrok, Cloud Run, etc.) e ajustar o redirect URI.

---

## 8. Exemplos de uso

```bash
# Rodando localmente
python tools/whoami.py
# Acesse http://localhost:8080/ e faça login

# Usando o arquivo gerado no alerta
export MENTIONS_PATH=./mentions-web.json
python3.9 main.py --dry-run
```
