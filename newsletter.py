# Radar de IA — newsletter automatizada

Pipeline que coleta notícias de IA de feeds RSS, faz a curadoria e o resumo em português com o Claude, monta uma newsletter em HTML e envia por e-mail a cada 2 dias. Roda de graça no GitHub Actions.

## Como funciona

`coleta (RSS) → filtro + dedup → curadoria/resumo (Claude) → HTML → e-mail → arquivo + estado`

- **`config.json`** — fontes, palavras-chave e parâmetros. É aqui que você evolui a newsletter, sem tocar no código.
- **`newsletter.py`** — o pipeline.
- **`.github/workflows/newsletter.yml`** — agendamento (cron a cada 2 dias).
- **`state/seen_urls.json`** — memória de links já enviados (evita repetição entre execuções).
- **`archive/`** — cada edição fica salva em HTML (log + histórico).

## Setup (uma vez)

1. Crie um repositório **privado** no GitHub e suba estes arquivos.
2. Em **Settings → Secrets and variables → Actions → New repository secret**, cadastre:

   | Secret | O que é |
   |---|---|
   | `ANTHROPIC_API_KEY` | Sua chave da API do Claude (console.anthropic.com) |
   | `SMTP_HOST` | Servidor SMTP (ex.: `smtp.gmail.com`) |
   | `SMTP_PORT` | `465` (SSL) ou `587` (STARTTLS) |
   | `SMTP_USER` | Usuário/login do e-mail |
   | `SMTP_PASS` | Senha de app (no Gmail, gere uma *App Password*; a senha normal não funciona com 2FA) |
   | `EMAIL_FROM` | Remetente (pode ser igual ao `SMTP_USER`) |
   | `EMAIL_TO` | Destinatário(s), separados por vírgula |

3. Em **Actions**, habilite os workflows do repositório.
4. Teste manualmente: aba **Actions → Radar de IA → Run workflow**.

## Testar localmente

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...
export DRY_RUN=1            # gera o HTML em archive/ sem enviar e-mail
python newsletter.py
```

Sem `DRY_RUN`, defina também as variáveis SMTP/EMAIL para enviar de verdade.

## Ajuste das fontes (importante)

Alguns feeds de blogs institucionais (OpenAI, DeepMind, Anthropic, Hugging Face) e veículos PT-BR vêm marcados com `"ativo": false` porque **as URLs de RSS mudam com frequência e precisam ser validadas**. Para cada um: confirme a URL do feed no site, corrija em `config.json` e mude para `"ativo": true`. O script registra no log quais feeds falharam ao carregar, então dá para ir ativando aos poucos.

- `"filtrar": true` → feed geral (The Verge, Ars): só passam itens que batem com `palavras_chave`.
- `"filtrar": false` → feed já específico de IA (arXiv cs.AI, TechCrunch/IA): passa tudo.

## Custo

O resumo roda no **Claude Haiku 4.5** (US$ 1 / US$ 5 por milhão de tokens de entrada/saída). Uma edição consome poucas dezenas de milhares de tokens — na prática, centavos por edição, algo abaixo de US$ 2–3/mês em ~15 edições. Para resumos mais elaborados, troque `"modelo"` por `"claude-sonnet-5"` no `config.json`.

## Melhoria contínua

- **Fontes**: adicione/remova feeds e ative os institucionais conforme a qualidade.
- **Relevância**: ajuste `palavras_chave`, `relevancia_minima` e `max_itens_edicao`.
- **Editorial**: o tom e a seleção vivem no `PROMPT_SISTEMA` (dentro de `newsletter.py`) — versione as mudanças.
- **Histórico**: `archive/` guarda todas as edições; use para revisar o que funcionou e realimentar o prompt.

## Próximos passos possíveis (v2)

- Enriquecer itens sem RSS via *web search tool* da API do Claude.
- Adicionar fontes que não têm RSS via NewsAPI/GNews.
- Guardar feedback ("útil/não útil") e usar para reordenar prioridades.
- Publicar o arquivo como página web além do e-mail.
