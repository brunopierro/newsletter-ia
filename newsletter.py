#!/usr/bin/env python3
"""
Radar de IA — pipeline de curadoria automatizada.

Fluxo: coleta (RSS) -> filtra/dedup -> cura + resume via Claude (PT-BR)
       -> monta HTML -> envia por e-mail -> arquiva e atualiza estado.

Rode local com: python newsletter.py
Em produção, o GitHub Actions dispara a cada 2 dias (ver .github/workflows/newsletter.yml).

Variáveis de ambiente necessárias:
  ANTHROPIC_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM, EMAIL_TO
Opcional:
  DRY_RUN=1  -> não envia e-mail, só gera o HTML no arquivo (para testes)
"""

import os
import sys
import json
import ssl
import html
import time
import smtplib
import datetime as dt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import feedparser
from anthropic import Anthropic

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "state", "seen_urls.json")
ARCHIVE_DIR = os.path.join(BASE, "archive")

PALETA = {"papel": "#faf8f3", "tinta": "#1a1a1a", "suave": "#5c5c5c",
          "coral": "#e2603f", "linha": "#e4ddd0", "cartao": "#ffffff"}


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def log(msg):
    print(f"[{dt.datetime.utcnow().isoformat(timespec='seconds')}Z] {msg}", flush=True)


def carregar_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def carregar_estado():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return set(json.load(f).get("urls", []))
        except Exception:
            return set()
    return set()


def salvar_estado(urls):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    # mantém no máximo os 2000 links mais recentes para o arquivo não crescer sem limite
    dados = {"atualizado": dt.datetime.utcnow().isoformat(), "urls": list(urls)[-2000:]}
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


def limpar_texto(texto, limite=600):
    """Remove tags HTML grosseiramente e normaliza espaços."""
    if not texto:
        return ""
    import re
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = html.unescape(texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:limite]


def data_do_item(entry):
    for campo in ("published_parsed", "updated_parsed"):
        val = entry.get(campo)
        if val:
            try:
                return dt.datetime.fromtimestamp(time.mktime(val), tz=dt.timezone.utc)
            except Exception:
                pass
    return None


def bate_palavra_chave(texto, chaves):
    t = texto.lower()
    return any(c in t for c in chaves)


# ----------------------------------------------------------------------------
# 1. Coleta
# ----------------------------------------------------------------------------
def coletar_candidatos(config, vistos):
    janela = dt.timedelta(days=config.get("janela_dias", 3))
    agora = dt.datetime.now(tz=dt.timezone.utc)
    chaves = [c.lower() for c in config.get("palavras_chave", [])]
    candidatos = []
    links_novos = set()

    for feed in config.get("feeds", []):
        if not feed.get("ativo", False):
            continue
        try:
            d = feedparser.parse(feed["url"])
            if d.get("bozo") and not d.entries:
                log(f"  ! feed falhou: {feed['fonte']} ({feed['url']})")
                continue
        except Exception as e:
            log(f"  ! erro ao ler {feed['fonte']}: {e}")
            continue

        pegos = 0
        for entry in d.entries:
            link = entry.get("link", "").strip()
            titulo = limpar_texto(entry.get("title", ""), 300)
            if not link or not titulo or link in vistos:
                continue

            data = data_do_item(entry)
            if data and (agora - data) > janela:
                continue

            resumo = limpar_texto(entry.get("summary", "") or entry.get("description", ""))

            if feed.get("filtrar", False) and not bate_palavra_chave(titulo + " " + resumo, chaves):
                continue

            candidatos.append({
                "fonte": feed["fonte"],
                "categoria_dica": feed.get("categoria_dica", "Outro"),
                "titulo": titulo,
                "link": link,
                "resumo": resumo,
                "data": data.isoformat() if data else "",
                "_ts": data.timestamp() if data else 0,
            })
            links_novos.add(link)
            pegos += 1
        log(f"  ok {feed['fonte']}: {pegos} itens")

    # dedup por link (mantém o primeiro) e ordena por recência
    unicos, seen_link = [], set()
    for c in sorted(candidatos, key=lambda x: x["_ts"], reverse=True):
        if c["link"] in seen_link:
            continue
        seen_link.add(c["link"])
        unicos.append(c)

    limite = config.get("max_candidatos", 50)
    return unicos[:limite], links_novos


# ----------------------------------------------------------------------------
# 2. Curadoria + resumo via Claude
# ----------------------------------------------------------------------------
PROMPT_SISTEMA = """Você é curador(a) editorial de uma newsletter sobre inteligência artificial, \
escrita em {idioma}. Recebe uma lista de itens candidatos (notícias, lançamentos, tutoriais, \
cursos e artigos de pesquisa) coletados de veículos de tecnologia e blogs de empresas de IA.

Sua tarefa:
1. SELECIONE os itens mais relevantes e noticiosos. Descarte duplicatas temáticas, \
   press releases vazios, conteúdo off-topic e itens de baixo interesse.
2. CLASSIFIQUE cada item selecionado em UMA categoria: \
   "Lançamento", "Tendência", "Tutorial", "Curso", "Pesquisa" ou "Outro".
3. RESUMA cada item em {idioma}, em 2 a 4 frases, de forma informativa e direta. \
   Baseie-se APENAS no título e no resumo fornecidos — não invente fatos, números ou citações.
4. Atribua "relevancia" de 1 a 5 (5 = mais relevante).
5. Escreva um "intro" curto (2 a 3 frases) contextualizando os destaques desta edição.

Selecione no máximo {max_itens} itens. Responda APENAS com JSON válido, sem markdown, \
sem cercas de código, sem texto antes ou depois. Use exatamente este formato:

{{"intro": "...", "itens": [{{"id": 0, "titulo_pt": "...", "categoria": "...", "resumo": "...", "relevancia": 4}}]}}

Devolva o campo "id" exatamente como recebido, para eu recuperar o link original."""


def extrair_json(texto):
    texto = texto.strip()
    if texto.startswith("```"):
        partes = texto.split("```")
        texto = partes[1] if len(partes) > 1 else texto
        if texto.lstrip().lower().startswith("json"):
            texto = texto.lstrip()[4:]
    i, j = texto.find("{"), texto.rfind("}")
    if i == -1 or j == -1:
        raise ValueError("Resposta do modelo não contém JSON.")
    return json.loads(texto[i:j + 1])


def curar(config, candidatos):
    client = Anthropic()  # usa ANTHROPIC_API_KEY do ambiente
    payload = [
        {"id": i, "fonte": c["fonte"], "titulo": c["titulo"], "resumo": c["resumo"]}
        for i, c in enumerate(candidatos)
    ]
    sistema = PROMPT_SISTEMA.format(
        idioma=config.get("idioma_resumo", "português do Brasil"),
        max_itens=config.get("max_itens_edicao", 15),
    )
    msg = client.messages.create(
        model=config.get("modelo", "claude-haiku-4-5-20251001"),
        max_tokens=4096,
        system=sistema,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    texto = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    dados = extrair_json(texto)

    minimo = config.get("relevancia_minima", 2)
    itens = []
    for it in dados.get("itens", []):
        idx = it.get("id")
        if not isinstance(idx, int) or idx < 0 or idx >= len(candidatos):
            continue
        if it.get("relevancia", 0) < minimo:
            continue
        base = candidatos[idx]
        itens.append({
            "titulo": it.get("titulo_pt", base["titulo"]),
            "categoria": it.get("categoria", base["categoria_dica"]),
            "resumo": it.get("resumo", ""),
            "relevancia": it.get("relevancia", 3),
            "fonte": base["fonte"],
            "link": base["link"],
            "data": base["data"],
        })

    ordem = {c: i for i, c in enumerate(config.get("categorias_ordem", []))}
    itens.sort(key=lambda x: (ordem.get(x["categoria"], 99), -x["relevancia"]))
    return dados.get("intro", ""), itens


# ----------------------------------------------------------------------------
# 3. Montagem do HTML
# ----------------------------------------------------------------------------
def montar_html(config, intro, itens):
    p = PALETA
    hoje = dt.datetime.now(tz=dt.timezone.utc).strftime("%d/%m/%Y")
    titulo = config.get("titulo_newsletter", "Radar de IA")

    grupos = {}
    for it in itens:
        grupos.setdefault(it["categoria"], []).append(it)
    ordem = config.get("categorias_ordem", list(grupos.keys()))
    cats = [c for c in ordem if c in grupos] + [c for c in grupos if c not in ordem]

    def esc(s):
        return html.escape(s or "")

    blocos = []
    for cat in cats:
        cards = []
        for it in grupos[cat]:
            cards.append(f"""
            <div style="background:{p['cartao']};border:1px solid {p['linha']};border-radius:10px;padding:18px 20px;margin:0 0 14px;">
              <a href="{esc(it['link'])}" style="color:{p['tinta']};text-decoration:none;font-size:17px;font-weight:600;line-height:1.35;">{esc(it['titulo'])}</a>
              <div style="color:{p['suave']};font-size:12px;margin:6px 0 10px;text-transform:uppercase;letter-spacing:.4px;">{esc(it['fonte'])}</div>
              <div style="color:{p['tinta']};font-size:14.5px;line-height:1.6;">{esc(it['resumo'])}</div>
              <a href="{esc(it['link'])}" style="display:inline-block;margin-top:10px;color:{p['coral']};font-size:13px;font-weight:600;text-decoration:none;">Ler no original &rarr;</a>
            </div>""")
        blocos.append(f"""
          <div style="margin:0 0 26px;">
            <div style="font-size:13px;font-weight:700;color:{p['coral']};text-transform:uppercase;letter-spacing:1px;border-bottom:2px solid {p['linha']};padding-bottom:6px;margin-bottom:14px;">{esc(cat)}</div>
            {''.join(cards)}
          </div>""")

    intro_bloco = f"""<p style="color:{p['suave']};font-size:15px;line-height:1.65;margin:0 0 26px;">{esc(intro)}</p>""" if intro else ""

    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{p['papel']};font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:640px;margin:0 auto;padding:32px 20px 48px;">
    <div style="border-bottom:3px solid {p['tinta']};padding-bottom:14px;margin-bottom:24px;">
      <div style="font-size:26px;font-weight:800;color:{p['tinta']};letter-spacing:-.5px;">{esc(titulo)}</div>
      <div style="font-size:13px;color:{p['suave']};margin-top:4px;">Curadoria de IA &middot; {hoje} &middot; {len(itens)} destaques</div>
    </div>
    {intro_bloco}
    {''.join(blocos)}
    <div style="border-top:1px solid {p['linha']};margin-top:20px;padding-top:16px;color:{p['suave']};font-size:11.5px;line-height:1.6;">
      Gerado automaticamente por curadoria com IA. Resumos podem conter imprecisões — confira sempre a fonte original.
    </div>
  </div>
</body></html>"""


def montar_texto(intro, itens):
    linhas = []
    if intro:
        linhas += [intro, ""]
    for it in itens:
        linhas += [f"[{it['categoria']}] {it['titulo']} — {it['fonte']}", it["resumo"], it["link"], ""]
    return "\n".join(linhas)


# ----------------------------------------------------------------------------
# 4. Envio
# ----------------------------------------------------------------------------
def enviar_email(config, html_corpo, texto_corpo):
    host = os.environ["SMTP_HOST"]
    porta = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ["SMTP_USER"]
    senha = os.environ["SMTP_PASS"]
    remetente = os.environ.get("EMAIL_FROM", user)
    destinos = [e.strip() for e in os.environ["EMAIL_TO"].split(",") if e.strip()]

    hoje = dt.datetime.now(tz=dt.timezone.utc).strftime("%d/%m")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{config.get('titulo_newsletter', 'Radar de IA')} — {hoje}"
    msg["From"] = remetente
    msg["To"] = ", ".join(destinos)
    msg.attach(MIMEText(texto_corpo, "plain", "utf-8"))
    msg.attach(MIMEText(html_corpo, "html", "utf-8"))

    ctx = ssl.create_default_context()
    if porta == 465:
        with smtplib.SMTP_SSL(host, porta, context=ctx) as s:
            s.login(user, senha)
            s.sendmail(remetente, destinos, msg.as_string())
    else:
        with smtplib.SMTP(host, porta) as s:
            s.starttls(context=ctx)
            s.login(user, senha)
            s.sendmail(remetente, destinos, msg.as_string())
    log(f"E-mail enviado para {len(destinos)} destinatário(s).")


# ----------------------------------------------------------------------------
# Orquestração
# ----------------------------------------------------------------------------
def main():
    config = carregar_config()
    vistos = carregar_estado()
    log("Coletando feeds...")
    candidatos, links_novos = coletar_candidatos(config, vistos)
    log(f"{len(candidatos)} candidatos após filtro/dedup.")

    if not candidatos:
        log("Nada novo nesta janela. Encerrando sem enviar.")
        return

    log("Curando com Claude...")
    intro, itens = curar(config, candidatos)
    log(f"{len(itens)} itens selecionados para a edição.")

    if not itens:
        log("Curadoria não retornou itens acima do corte de relevância. Encerrando.")
        # ainda marca como vistos para não reprocessar o mesmo lote
        salvar_estado(vistos | links_novos)
        return

    html_corpo = montar_html(config, intro, itens)
    texto_corpo = montar_texto(intro, itens)

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    stamp = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%d")
    arquivo = os.path.join(ARCHIVE_DIR, f"edicao-{stamp}.html")
    with open(arquivo, "w", encoding="utf-8") as f:
        f.write(html_corpo)
    log(f"Edição arquivada em {arquivo}")

    if os.environ.get("DRY_RUN") == "1":
        log("DRY_RUN ativo — e-mail NÃO enviado.")
    else:
        enviar_email(config, html_corpo, texto_corpo)

    salvar_estado(vistos | links_novos)
    log("Concluído.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"ERRO FATAL: {e}")
        sys.exit(1)
