# Radar de IA — newsletter semanal (v2)

Newsletter semanal de IA com 8 blocos. O robô busca na web, escreve cada seção seguindo regras editoriais, te manda uma **prévia para revisar** e publica quando você aprova. Roda no GitHub Actions.

## O fluxo da semana (é só isto)

1. **Segunda de manhã, sozinho:** o robô monta a edição inteira e te envia por e-mail uma **prévia** com o assunto `[RASCUNHO]`. As seções que merecem sua atenção vêm marcadas com 📝.
2. **Você revisa** quando puder. Se estiver tudo bom, não precisa fazer nada além do passo 3. Se quiser mudar alguma seção, veja "Editar uma seção" abaixo.
3. **Publicar:** vá em **Actions → "Radar de IA — Publicar edição" → Run workflow**. A edição vai para a sua lista de destinatários.

Se numa semana você não publicar, nada de ruim acontece — simplesmente aquela edição não sai.

## As 8 seções

Abertura (carta do editor) · 🔭 Tendências · 📈 Mercado de IA · 🛠 Usabilidade · ⚡ Lançamentos e Novidades · 📚 Para Ler com Calma · 🎓 Cursos · 📖 Livros.

As analíticas (Tendências, Mercado, Lançamentos) saem prontas. As de julgamento (Usabilidade, Para Ler, Cursos, Livros) vêm marcadas 📝 para você conferir — porque livro, curso e "ferramenta que funciona" pedem olho humano.

## Regras de integridade (embutidas no robô)

- Todo item precisa de uma **fonte real com link**; sem isso, é descartado, nunca inventado.
- **Benchmarks do fabricante** são rotulados como afirmação da empresa, não como prova.
- Em Usabilidade, os casos vêm marcados como **"pistas a testar"** — o robô não testa nada.

## Instalar / atualizar (vindo da v1)

Suba estes arquivos para o mesmo repositório, substituindo os antigos (Add file → Upload files). Os arquivos novos/alterados são: `newsletter.py`, `config.json`, `requirements.txt`, a pasta `.github/workflows/` (agora com `rascunho.yml` e `publicar.yml` — pode apagar o `newsletter.yml` antigo) e a pasta `editorial/`.

**Passo obrigatório uma vez:** em **Settings → Actions → General**, na seção *Workflow permissions*, marque **"Read and write permissions"** e salve. Sem isso, o robô não consegue guardar o rascunho.

Os secrets (`ANTHROPIC_API_KEY`, `SMTP_*`, `EMAIL_*`) continuam os mesmos da v1 — não precisa recadastrar.

## Testar agora

Em **Actions → "Radar de IA — Montar rascunho" → Run workflow**. Em alguns minutos chega a prévia `[RASCUNHO]` no seu e-mail. Quando gostar, rode o **Publicar**.

## Editar uma seção com as suas palavras

Na pasta `editorial/` (veja o `LEIA-ME.md` lá dentro), crie um arquivo com o nome da seção — por exemplo `livros.md` — e escreva o seu texto. Se o arquivo tiver conteúdo, o robô usa o **seu** texto no lugar do dele. Depois, rode "Montar rascunho" de novo para ver na prévia. Para voltar ao automático, apague o arquivo.

## Ajustar fontes, tom e quantidade

Tudo em `config.json`: as `fontes` de cada seção, a `pergunta`, a `abordagem`, o `max_itens`, as `trilhas` (Cursos) e as `categorias` (Livros). Para mexer na "voz" de forma profunda, a função `montar_prompt()` no `newsletter.py` é onde os prompts são montados.

## Custo

Esta versão usa o **Claude Sonnet 5** (melhor qualidade para análise) e a **busca na web** da API, que tem custo por consulta. Estimativa: alguns dólares por mês em edições semanais. Para economizar, troque `"modelo"` por `"claude-haiku-4-5-20251001"` no `config.json`.

## Quando algo falhar

Cada seção é isolada: se uma falhar (fonte fora do ar, etc.), ela aparece como "— sem itens verificados nesta edição —" e o resto da edição sai normalmente. O registro (log) de cada execução, na aba Actions, mostra em português o que aconteceu em cada seção.
