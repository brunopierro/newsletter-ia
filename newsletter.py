#!/usr/bin/env python3
"""
Radar de IA — newsletter semanal (v2).

Modelo híbrido: as seções analíticas (Tendências, Mercado, Lançamentos) o robô monta
sozinho; as de julgamento (Usabilidade, Para Ler, Cursos, Livros) vêm como rascunho para
você revisar. O robô monta a edição inteira, te envia uma PRÉVIA e você publica com um clique.

Cada seção é escrita por uma chamada ao Claude com BUSCA NA WEB, seguindo as regras e a
estrutura definidas em config.json. Regra de ouro: nada sem fonte real — se não confirmar
com um link, o item é descartado, nunca inventado.

MODOS (variável de ambiente MODO):
  rascunho  -> gera a edição, salva em drafts/edicao-atual.html e envia PRÉVIA para você
  publicar  -> envia a edição já aprovada (drafts/edicao-atual.html) para a lista final

Variáveis de ambiente:
  ANTHROPIC_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM, EMAIL_TO
  MODO (rascunho|publicar)   DRY_RUN=1 (opcional: não envia e-mail)
"""

import os
import re
import sys
import ssl
import json
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
DRAFT_HTML = os.path.join(BASE, "drafts", "edicao-atual.html")
ARCHIVE_DIR = os.path.join(BASE, "archive")
EDITORIAL_DIR = os.path.join(BASE, "editorial")

P = {"papel": "#faf8f3", "tinta": "#1a1a1a", "suave": "#5c5c5c", "coral": "#e2603f",
     "linha": "#e4ddd0", "cartao": "#ffffff", "aviso": "#8a6d3b", "avisobg": "#fcf3e3"}

AGORA = dt.datetime.now(tz=dt.timezone.utc)


def log(m):
    print(f"[{AGORA.isoformat(timespec='seconds')}] {m}", flush=True)


def esc(s):
    return html.escape(str(s or ""))


def carregar_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def limpar(texto, limite=500):
    if not texto:
        return ""
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = html.unescape(texto)
    return re.sub(r"\s+", " ", texto).strip()[:limite]


def md_leve_para_html(texto):
    """Conversor mínimo de markdown -> HTML para os arquivos editorial/*.md."""
    partes = []
    for par in texto.strip().split("\n\n"):
        par = esc(par.strip())
        par = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", par)
        par = re.sub(r"\[(.+?)\]\((https?://[^\s)]+)\)",
                     rf'<a href="\2" style="color:{P["coral"]};">\1</a>', par)
        par = par.replace("\n", "<br>")
        if par:
            partes.append(f'<p style="margin:0 0 12px;line-height:1.65;">{par}</p>')
    return "".join(partes)


def override_editorial(secao_id):
    """Se editorial/<id>.md existir e tiver conteúdo, o texto do editor substitui a geração."""
    caminho = os.path.join(EDITORIAL_DIR, f"{secao_id}.md")
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            txt = f.read().strip()
        if txt:
            return txt
    return None


# ----------------------------------------------------------------------------
# Coleta de RSS (apenas para dar contexto à seção de Lançamentos)
# ----------------------------------------------------------------------------
def coletar_rss(config):
    janela = dt.timedelta(days=config.get("janela_dias", 8))
    chaves = [c.lower() for c in config.get("palavras_chave", [])]
    itens = []
    for feed in config.get("feeds_lancamentos", []):
        if not feed.get("ativo"):
            continue
        try:
            d = feedparser.parse(feed["url"])
        except Exception as e:
            log(f"  ! RSS falhou {feed['fonte']}: {e}")
            continue
        for e in d.entries[:25]:
            titulo = limpar(e.get("title", ""), 200)
            link = e.get("link", "")
            if not titulo or not link:
                continue
            data = None
            for c in ("published_parsed", "updated_parsed"):
                if e.get(c):
                    data = dt.datetime.fromtimestamp(time.mktime(e[c]), tz=dt.timezone.utc)
                    break
            if data and (AGORA - data) > janela:
                continue
            resumo = limpar(e.get("summary", ""), 300)
            if feed.get("filtrar") and not any(k in (titulo + resumo).lower() for k in chaves):
                continue
            itens.append(f"- {titulo} ({feed['fonte']}) {link}")
    return "\n".join(itens[:40])


# ----------------------------------------------------------------------------
# Chamada ao Claude com busca na web
# ----------------------------------------------------------------------------
GUARDRAILS = """REGRAS INEGOCIÁVEIS:
- Use a ferramenta de busca na web para encontrar informação REAL e recente. Não confie na memória.
- Todo item DEVE ter uma URL real encontrada na busca. Se não conseguir confirmar com uma fonte, NÃO inclua o item.
- Nunca invente títulos, autores, preços, valores, datas ou benchmarks.
- Afirmações de desempenho/benchmarks: atribua explicitamente ao fabricante; só trate como comprovado se houver fonte independente.
- Se não houver nada relevante e verificável nesta janela, devolva a lista vazia (não force conteúdo).
- Escreva em {idioma}. Responda SOMENTE com JSON válido — sem markdown, sem cercas de código, sem texto fora do JSON."""

ESQUEMAS = {
    "analise": '{"analise": "2 a 3 parágrafos de análise autoral sobre a mudança estrutural", "fontes": [{"titulo": "", "url": ""}]}',
    "movimentos": '{"intro": "1 frase", "itens": [{"movimento": "", "resumo": "2-3 frases", "dado": "valor/número quando houver", "fonte": "", "url": ""}]}',
    "leads": '{"itens": [{"problema": "", "metodo": "", "resultado_relatado": "", "quem_relatou": "", "url": ""}]}',
    "notas": '{"itens": [{"titulo": "", "o_que_e": "1-2 frases", "empresa_afirma": "", "verificacao_independente": "texto ou \'ainda não disponível\'", "url": ""}]}',
    "leituras": '{"itens": [{"titulo": "", "autor_veiculo": "", "por_que_selecionamos": "", "grande_questao": "", "tempo_leitura": "", "nivel": "introdutório|intermediário|especializado", "url": ""}]}',
    "cursos": '{"trilhas": [{"trilha": "nome exato da trilha", "cursos": [{"curso": "", "instituicao": "", "o_que_aprende": "", "preco": "Gratuito|Pago|valor", "por_que_vale": "", "url": ""}]}]}',
    "livros": '{"categorias": [{"categoria": "nome exato da categoria", "livros": [{"livro": "", "autores": "", "por_que_ler_agora": "", "resumo": "até 5 linhas", "url": ""}]}]}',
}


def montar_prompt(config, secao, rss_ctx=""):
    idioma = config.get("idioma", "português do Brasil")
    fontes = "; ".join(secao.get("fontes", []))
    extras = ""
    if secao.get("trilhas"):
        extras += "\nTrilhas (use exatamente estes nomes): " + " | ".join(secao["trilhas"])
    if secao.get("categorias"):
        extras += "\nCategorias (use exatamente estes nomes): " + " | ".join(secao["categorias"])
    if secao["formato"] == "leads":
        extras += "\nLembre: são PISTAS A TESTAR, não recomendações testadas."
    if secao.get("usa_rss") and rss_ctx:
        extras += "\n\nItens recentes de RSS que podem servir de ponto de partida (verifique e complemente com busca):\n" + rss_ctx

    sistema = f"""Você é editor(a) de uma newsletter brasileira sobre inteligência artificial, seção "{secao['nome']}".
Pergunta que a seção responde: {secao['pergunta']}
Abordagem editorial: {secao['abordagem']}
Priorize estas fontes (busque por elas e por cobertura recente): {fontes}.
Selecione no máximo {secao.get('max_itens', 5)} itens (os mais relevantes e recentes).{extras}

""" + GUARDRAILS.format(idioma=idioma) + f"""

Formato EXATO da resposta (JSON):
{ESQUEMAS[secao['formato']]}"""

    usuario = f"Monte a seção \"{secao['nome']}\" da edição de {AGORA.strftime('%d/%m/%Y')}. Busque informação recente e verificável e responda só com o JSON."
    return sistema, usuario


def chamar_claude(client, model, sistema, usuario, buscar=True):
    mensagens = [{"role": "user", "content": usuario}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}] if buscar else []
    resp = None
    for _ in range(5):
        resp = client.messages.create(
            model=model, max_tokens=4096, system=sistema, messages=mensagens, tools=tools,
        )
        if resp.stop_reason == "pause_turn":
            mensagens.append({"role": "assistant", "content": resp.content})
            continue
        break
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def extrair_json(texto):
    texto = texto.strip()
    if texto.startswith("```"):
        p = texto.split("```")
        texto = p[1] if len(p) > 1 else texto
        if texto.lstrip().lower().startswith("json"):
            texto = texto.lstrip()[4:]
    i, j = texto.find("{"), texto.rfind("}")
    if i == -1 or j == -1:
        raise ValueError("sem JSON na resposta")
    return json.loads(texto[i:j + 1])


def gerar_secao(client, config, secao, rss_ctx=""):
    """Retorna (html_do_corpo, sem_conteudo_bool). Nunca lança exceção para fora."""
    ov = override_editorial(secao["id"])
    if ov:
        log(f"  · {secao['nome']}: usando texto de editorial/{secao['id']}.md")
        return md_leve_para_html(ov), False
    try:
        sistema, usuario = montar_prompt(config, secao, rss_ctx)
        texto = chamar_claude(client, config.get("modelo"), sistema, usuario, buscar=True)
        dados = extrair_json(texto)
        corpo = RENDERERS[secao["formato"]](dados)
        vazio = not corpo.strip()
        return (corpo if not vazio else placeholder()), vazio
    except Exception as e:
        log(f"  ! {secao['nome']} falhou: {e}")
        return placeholder(), True


def placeholder():
    return f'<p style="color:{P["suave"]};font-style:italic;">— sem itens verificados nesta edição —</p>'


# ----------------------------------------------------------------------------
# Renderizadores por formato
# ----------------------------------------------------------------------------
def _card(inner):
    return f'<div style="background:{P["cartao"]};border:1px solid {P["linha"]};border-radius:10px;padding:16px 18px;margin:0 0 12px;">{inner}</div>'


def _link(url, texto="Ler no original &rarr;"):
    if not url:
        return ""
    return f'<a href="{esc(url)}" style="display:inline-block;margin-top:8px;color:{P["coral"]};font-size:13px;font-weight:600;text-decoration:none;">{texto}</a>'


def r_analise(d):
    corpo = md_leve_para_html(d.get("analise", ""))
    fontes = d.get("fontes", [])
    if fontes:
        links = " · ".join(f'<a href="{esc(f.get("url"))}" style="color:{P["coral"]};">{esc(f.get("titulo") or "fonte")}</a>' for f in fontes if f.get("url"))
        if links:
            corpo += f'<p style="font-size:12.5px;color:{P["suave"]};margin-top:10px;">Fontes: {links}</p>'
    return corpo


def r_movimentos(d):
    out = ""
    if d.get("intro"):
        out += f'<p style="color:{P["suave"]};margin:0 0 12px;">{esc(d["intro"])}</p>'
    for it in d.get("itens", []):
        dado = f' <span style="color:{P["coral"]};font-weight:600;">{esc(it["dado"])}</span>' if it.get("dado") else ""
        out += _card(
            f'<div style="font-size:16px;font-weight:600;">{esc(it.get("movimento"))}{dado}</div>'
            f'<div style="font-size:14px;line-height:1.55;margin-top:6px;">{esc(it.get("resumo"))}</div>'
            + _link(it.get("url"), f'{esc(it.get("fonte") or "fonte")} &rarr;'))
    return out


def r_leads(d):
    if not d.get("itens"):
        return ""
    aviso = (f'<div style="background:{P["avisobg"]};color:{P["aviso"]};font-size:12.5px;'
             f'padding:8px 12px;border-radius:8px;margin-bottom:12px;">⚠️ Relatos e métodos de terceiros — '
             f'<strong>pistas a testar</strong>, ainda não verificados pela redação.</div>')
    out = aviso
    for it in d.get("itens", []):
        out += _card(
            f'<div style="font-size:15px;font-weight:600;">{esc(it.get("problema"))}</div>'
            f'<div style="font-size:14px;line-height:1.55;margin-top:6px;"><strong>Método:</strong> {esc(it.get("metodo"))}</div>'
            f'<div style="font-size:14px;line-height:1.55;margin-top:4px;"><strong>Resultado relatado:</strong> {esc(it.get("resultado_relatado"))}</div>'
            f'<div style="font-size:12.5px;color:{P["suave"]};margin-top:6px;">Relatado por: {esc(it.get("quem_relatou"))}</div>'
            + _link(it.get("url")))
    return out


def r_notas(d):
    out = ""
    for it in d.get("itens", []):
        vi = it.get("verificacao_independente") or "ainda não disponível"
        out += _card(
            f'<div style="font-size:15px;font-weight:600;">{esc(it.get("titulo"))}</div>'
            f'<div style="font-size:14px;line-height:1.5;margin-top:4px;">{esc(it.get("o_que_e"))}</div>'
            f'<div style="font-size:12.5px;color:{P["suave"]};margin-top:6px;"><strong>Fabricante afirma:</strong> {esc(it.get("empresa_afirma"))}</div>'
            f'<div style="font-size:12.5px;color:{P["suave"]};margin-top:2px;"><strong>Verificação independente:</strong> {esc(vi)}</div>'
            + _link(it.get("url")))
    return out


def r_leituras(d):
    out = ""
    for it in d.get("itens", []):
        out += _card(
            f'<div style="font-size:16px;font-weight:600;">{esc(it.get("titulo"))}</div>'
            f'<div style="font-size:12.5px;color:{P["suave"]};margin:4px 0 8px;">{esc(it.get("autor_veiculo"))}</div>'
            f'<div style="font-size:14px;line-height:1.55;"><strong>Por que selecionamos:</strong> {esc(it.get("por_que_selecionamos"))}</div>'
            f'<div style="font-size:14px;line-height:1.55;margin-top:4px;"><strong>A grande questão:</strong> {esc(it.get("grande_questao"))}</div>'
            f'<div style="font-size:12.5px;color:{P["suave"]};margin-top:6px;">⏱ {esc(it.get("tempo_leitura"))} &middot; Nível: {esc(it.get("nivel"))}</div>'
            + _link(it.get("url")))
    return out


def r_cursos(d):
    out = ""
    for tr in d.get("trilhas", []):
        cursos = tr.get("cursos", [])
        if not cursos:
            continue
        out += f'<div style="font-size:13px;font-weight:700;color:{P["tinta"]};margin:14px 0 8px;">▸ {esc(tr.get("trilha"))}</div>'
        for c in cursos:
            out += _card(
                f'<div style="font-size:15px;font-weight:600;">{esc(c.get("curso"))}</div>'
                f'<div style="font-size:12.5px;color:{P["suave"]};margin:3px 0 6px;">{esc(c.get("instituicao"))} &middot; <strong>{esc(c.get("preco"))}</strong></div>'
                f'<div style="font-size:14px;line-height:1.5;"><strong>O que você aprende:</strong> {esc(c.get("o_que_aprende"))}</div>'
                f'<div style="font-size:14px;line-height:1.5;margin-top:4px;"><strong>Por que vale:</strong> {esc(c.get("por_que_vale"))}</div>'
                + _link(c.get("url"), "Ver o curso &rarr;"))
    return out


def r_livros(d):
    out = ""
    for cat in d.get("categorias", []):
        livros = cat.get("livros", [])
        if not livros:
            continue
        out += f'<div style="font-size:13px;font-weight:700;color:{P["tinta"]};margin:14px 0 8px;">▸ {esc(cat.get("categoria"))}</div>'
        for b in livros:
            out += _card(
                f'<div style="font-size:15px;font-weight:600;">{esc(b.get("livro"))}</div>'
                f'<div style="font-size:12.5px;color:{P["suave"]};margin:3px 0 6px;">{esc(b.get("autores"))}</div>'
                f'<div style="font-size:14px;line-height:1.5;"><strong>Por que ler agora:</strong> {esc(b.get("por_que_ler_agora"))}</div>'
                f'<div style="font-size:14px;line-height:1.55;margin-top:4px;">{esc(b.get("resumo"))}</div>'
                + _link(b.get("url"), "Ver o livro &rarr;"))
    return out


RENDERERS = {"analise": r_analise, "movimentos": r_movimentos, "leads": r_leads,
             "notas": r_notas, "leituras": r_leituras, "cursos": r_cursos, "livros": r_livros}


# ----------------------------------------------------------------------------
# Abertura (carta do editor)
# ----------------------------------------------------------------------------
def gerar_abertura(client, config, resumo_edicao):
    ov = override_editorial("abertura")
    if ov:
        return md_leve_para_html(ov)
    try:
        sistema = (f"Você é o editor de uma newsletter de IA em {config.get('idioma')}. "
                   "Escreva uma abertura curta (3 a 5 frases), tom de carta ao leitor, "
                   "identificando a questão MAIS IMPORTANTE da semana a partir do conteúdo abaixo. "
                   "Não invente fatos além do resumo. Responda só com o texto, sem título.")
        texto = chamar_claude(client, config.get("modelo"), sistema, resumo_edicao, buscar=False)
        return md_leve_para_html(texto.strip())
    except Exception as e:
        log(f"  ! abertura falhou: {e}")
        return ""


# ----------------------------------------------------------------------------
# Montagem do e-mail
# ----------------------------------------------------------------------------
def montar_html(config, abertura_html, secoes_render, rascunho=False):
    hoje = AGORA.strftime("%d/%m/%Y")
    titulo = config.get("titulo", "Radar de IA")
    assinatura = config.get("editor_nome", "")

    faixa = ""
    if rascunho:
        faixa = (f'<div style="background:{P["avisobg"]};color:{P["aviso"]};font-size:13px;'
                 f'padding:10px 14px;border-radius:8px;margin-bottom:20px;text-align:center;">'
                 f'PRÉVIA PARA REVISÃO — as seções marcadas com 📝 merecem sua atenção antes de publicar.</div>')

    blocos = []
    for nome, emoji, corpo, revisar in secoes_render:
        marca = " 📝" if (rascunho and revisar) else ""
        blocos.append(
            f'<div style="margin:0 0 30px;">'
            f'<div style="font-size:18px;font-weight:800;color:{P["tinta"]};border-bottom:2px solid {P["linha"]};padding-bottom:8px;margin-bottom:14px;">{emoji} {esc(nome)}{marca}</div>'
            f'{corpo}</div>')

    ab = f'<div style="font-size:15px;line-height:1.7;color:{P["suave"]};margin:0 0 30px;">{abertura_html}{("<div style=\"margin-top:8px;font-style:italic;\">— " + esc(assinatura) + "</div>") if assinatura else ""}</div>' if abertura_html else ""

    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:{P['papel']};font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
<div style="max-width:660px;margin:0 auto;padding:32px 20px 48px;">
  {faixa}
  <div style="border-bottom:3px solid {P['tinta']};padding-bottom:14px;margin-bottom:26px;">
    <div style="font-size:28px;font-weight:800;letter-spacing:-.5px;">{esc(titulo)}</div>
    <div style="font-size:13px;color:{P['suave']};margin-top:4px;">Edição semanal &middot; {hoje}</div>
  </div>
  {ab}
  {''.join(blocos)}
  <div style="border-top:1px solid {P['linha']};margin-top:24px;padding-top:16px;color:{P['suave']};font-size:11.5px;line-height:1.6;">
    Curadoria assistida por IA com verificação por fontes. Benchmarks de fabricantes são identificados como tais; relatos de terceiros são marcados como pistas a testar. Confira sempre a fonte original.
  </div>
</div></body></html>"""


def enviar(config, assunto, corpo_html, destinos):
    host = os.environ["SMTP_HOST"]
    porta = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ["SMTP_USER"]
    senha = os.environ["SMTP_PASS"]
    remetente = os.environ.get("EMAIL_FROM", user)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"] = remetente
    msg["To"] = ", ".join(destinos)
    msg.attach(MIMEText("Sua newsletter está em HTML. Abra num cliente compatível.", "plain", "utf-8"))
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))
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
def modo_rascunho(config):
    client = Anthropic()
    rss_ctx = coletar_rss(config)
    log(f"RSS coletado ({len(rss_ctx.splitlines())} itens de contexto).")

    secoes_render, resumo = [], []
    for secao in config.get("secoes", []):
        log(f"Gerando: {secao['nome']}...")
        corpo, vazio = gerar_secao(client, config, secao, rss_ctx)
        secoes_render.append((secao["nome"], secao["emoji"], corpo, secao.get("revisar", False)))
        if not vazio:
            resumo.append(f"{secao['nome']}: {re.sub(r'<[^>]+>', ' ', corpo)[:300]}")

    log("Gerando abertura...")
    abertura = gerar_abertura(client, config, "\n".join(resumo)[:4000])

    html_final = montar_html(config, abertura, secoes_render, rascunho=True)
    os.makedirs(os.path.dirname(DRAFT_HTML), exist_ok=True)
    with open(DRAFT_HTML, "w", encoding="utf-8") as f:
        f.write(html_final)
    log(f"Rascunho salvo em {DRAFT_HTML}")

    if os.environ.get("DRY_RUN") == "1":
        log("DRY_RUN — prévia não enviada.")
        return
    destino = [os.environ.get("EMAIL_FROM") or os.environ["SMTP_USER"]]
    enviar(config, f"[RASCUNHO] {config.get('titulo')} — {AGORA.strftime('%d/%m')}", html_final, destino)


def modo_publicar(config):
    if not os.path.exists(DRAFT_HTML):
        log("ERRO: não há rascunho (drafts/edicao-atual.html). Rode o modo rascunho antes.")
        sys.exit(1)
    with open(DRAFT_HTML, encoding="utf-8") as f:
        html_final = f.read()
    # remove a faixa/marcas de rascunho para a versão pública
    html_final = re.sub(r'PRÉVIA PARA REVISÃO.*?publicar\.</div>', '', html_final, flags=re.S)
    html_final = html_final.replace(" 📝", "")

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    arq = os.path.join(ARCHIVE_DIR, f"edicao-{AGORA.strftime('%Y-%m-%d')}.html")
    with open(arq, "w", encoding="utf-8") as f:
        f.write(html_final)

    if os.environ.get("DRY_RUN") == "1":
        log("DRY_RUN — edição não enviada.")
        return
    destinos = [e.strip() for e in os.environ["EMAIL_TO"].split(",") if e.strip()]
    enviar(config, f"{config.get('titulo')} — {AGORA.strftime('%d/%m')}", html_final, destinos)


def main():
    config = carregar_config()
    modo = os.environ.get("MODO", "rascunho").strip().lower()
    log(f"MODO = {modo}")
    if modo == "publicar":
        modo_publicar(config)
    else:
        modo_rascunho(config)
    log("Concluído.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"ERRO FATAL: {e}")
        sys.exit(1)
