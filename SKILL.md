---
name: one-page-report
description: 'Métricas de fluxo (Cycle Time, Throughput, WIP, Aging WIP, CFD, Lei de Little, segundo Vacanti) e previsão Monte Carlo (Magennis) a partir do CSV do Jira ou do Azure DevOps, em dois modos: o One Page Report de fim de sprint (HTML editável, 1 página A4) e a análise rápida, que responde só a pergunta, sem sprint e sem report (serve para Kanban). Use sempre que um Scrum Master, agilista ou PO quiser reportar uma sprint ou tirar uma dúvida de fluxo com dados, mesmo sem dizer "one page report". Exemplos: "monta o report da sprint 42", "status da sprint para o PO", "como está o fluxo do time", "o throughput caiu?", "quais itens estão envelhecendo", "quando terminamos esses 30 itens?", "quantos itens cabem até dezembro?", "e se o escopo crescer 20%?", "previsão só das histórias". NÃO é o report mensal para C-level nem o status semanal a partir de notas soltas, sem CSV.'
compatibility: Python 3.9+ com pandas>=2.0, numpy e matplotlib.
---

# One Page Report de Sprint

Relatório de uma página para PO, gestor do projeto e liderança operacional. Os números saem de `scripts/flow_report.py`; a narrativa sai desses números e das notas do Scrum Master. Esse modelo existe para que qualquer número do report possa ser rastreado até o CSV. Um número "estimado" pelo modelo destrói a confiança no report inteiro.

## 0. Escolher o modo

- **Report** (seções 1 a 5): o usuário quer fechar ou reportar uma sprint para alguém.
- **Análise rápida** (seção 6): o usuário fez uma pergunta pontual de fluxo ("o throughput caiu?", "o que está envelhecendo?", "quando terminamos 30 itens?"), o time trabalha sem sprint, ou não há datas de sprint. Não gere o report nem peça datas de sprint nesse caso: quem pergunta uma coisa quer a resposta, não um documento.

Na dúvida, responda a pergunta no modo rápido e ofereça o report em uma linha.

## 1. Coletar entradas

Necessário:
- **CSV** do Jira ou do Azure DevOps com os itens concluídos das últimas ~12 semanas e os itens em andamento.
- **Datas da sprint** (início e fim), **nome da sprint** e **nome do time**.

Opcional. Pergunte uma única vez, numa só mensagem, e apenas o que faltar:
- Objetivo da sprint e se foi atingido.
- Notas: destaques, impedimentos, decisões necessárias, foco da próxima sprint.
- **Itens restantes** até a release (responde "quando terminamos?") e/ou **data-alvo** (responde "quantos itens até lá?").
- Se houver previsão: **quanto o escopo costuma crescer** (itens que se dividem ou surgem no caminho) e **de que tipos são os itens restantes** (ex.: 20 histórias e 10 bugs). Veja a seção 2.1.
- Texto do campo Sprint/Iteration, para calcular planejado × entregue (ex.: "Sprint 42").
- Nome do autor do report.

Se o usuário não souber exportar, ou se faltar a data de início, consulte `references/export.md`.

## 2. Rodar a análise

O script fica em `scripts/flow_report.py`, dentro da pasta desta skill. Rode-o direto, sem copiar nem reescrever o código:

```bash
python <pasta-da-skill>/scripts/flow_report.py analyze --csv EXPORT.csv \
  --sprint-start AAAA-MM-DD --sprint-end AAAA-MM-DD --sprint-name "Sprint 42" --team "Time X" \
  [--dayfirst] [--backlog 30] [--target-date AAAA-MM-DD] [--sprint-filter "Sprint 42"] \
  [--window-weeks 12] [--map start="Nome da coluna"] --out-dir out
```

- Use `python` ou `python3`, o que existir no sistema. No Windows costuma ser `python`. Se faltar biblioteca: `pip install pandas numpy matplotlib`. Só acrescente `--break-system-packages` se o pip recusar por causa de um Python gerenciado pelo sistema.
- **Datas:** abra as primeiras linhas do CSV. Com `dd/mm` use `--dayfirst`; com `m/d` (ADO em inglês), não. Um erro aqui gera datas silenciosamente erradas.
- **Colunas:** o script reconhece Jira/ADO e nomes em português e inglês. Confira `column_mapping` na saída. Se algo foi mapeado errado ou não foi achado, rode de novo com `--map campo="Coluna"` (campos: id, title, type, status, created, start, done, sprint).
- **Qualidade:** leia `quality_notes`. Todas as notas vão para o rodapé do report automaticamente. As que mudam a conclusão (ex.: Cycle Time virou Lead Time, amostra pequena) também entram na leitura dos dados.
- **Amostra:** uma sprint sozinha tem poucos itens; por isso o script normaliza o throughput por semana e calcula os percentis numa janela móvel. Com menos de 20 itens na janela, aumente `--window-weeks`, se o export tiver histórico mais antigo, ou avise que a confiabilidade é baixa. Se o export começar depois do início da janela, o script encurta a janela sozinho, para que semanas sem dados não entrem como zero.

### 2.1 Previsão: crescimento de escopo e tipos de item

**Crescimento de escopo (`--growth`).** Backlog quase nunca termina do tamanho que começou: itens se dividem e surgem descobertas. Sem esse fator, a previsão "quando" sai otimista.
- Formato: `--growth 1.2` (fixo, +20%) ou `--growth 1.1-1.4` (faixa; cada simulação sorteia um fator dentro dela, o que já embute a incerteza).
- De onde tirar: do histórico do time ("começamos a release com 40 itens e entregamos 52" → 1.3). Sem histórico, pergunte ao usuário e sugira uma faixa como 1.1-1.3, dizendo que é premissa e não dado.
- No "quantos itens até a data", o script devolve `items` (do escopo de hoje, já descontado o crescimento) e `items_total` (vazão bruta). Comunique `items`: é o que cabe do que existe hoje.
- Não aplique `--growth` sem o usuário saber. É uma premissa que muda a data, e ele precisa poder defendê-la.

**Tipos de item.** O script sempre calcula `by_type` (entregas, participação e P50/P85/P95 por tipo). Quando o P85 de um tipo é pelo menos o dobro do de outro, ele grava `type_spread` e adiciona um aviso. Bugs de 2 dias e histórias de 14 dias misturados na mesma amostra deixam a previsão sem sentido para os dois.
- `--forecast-types "História"`: a previsão usa só o throughput desses tipos. Use quando os itens restantes são todos de um tipo.
- `--backlog-by-type "História=20,Bug=10"`: substitui `--backlog`. Simula os tipos juntos, sorteando os mesmos dias, porque eles disputam a mesma capacidade do time; termina quando todos os tipos chegam ao alvo.
- Os nomes são os do CSV (sem diferenciar maiúsculas). Se o tipo não existir, o script lista os tipos disponíveis.

## 3. Escrever a narrativa (`out/narrative.json`)

```json
{
 "author": "Nome (Scrum Master)",
 "status": "verde | amarelo | vermelho",
 "status_label": "Frase curta, ex.: Atenção: 2 itens travados",
 "sprint_goal": "...", "goal_result": "Atingido | Parcialmente | Não atingido",
 "reading": ["3 a 5 frases"],
 "highlights": [], "risks": [], "decisions": [], "next": []
}
```

**reading (Leitura dos dados):** 3 a 5 frases. Cada uma traz um número do `metrics.json` seguido do "e daí?" para quem lê. Ordem sugerida:
1. Entrega da sprint vs. média semanal.
2. Estabilidade do Cycle Time: P85 da sprint vs. P85 da janela.
3. Itens acima do P85, citados por ID e idade. O Aging WIP é o sinal mais acionável do report, porque aponta onde agir agora.
4. Estabilidade do sistema (entradas × saídas, Lei de Little) e se dá para confiar na previsão.

O público é PO, gestor e liderança, então traduza o jargão: "85% dos itens terminam em até 9 dias" em vez de "P85 = 9".

**highlights, risks, decisions, next:** preencha apenas com as notas do usuário ou com fatos do CSV (ex.: um item envelhecendo vira risco). Sem notas, deixe as listas vazias e avise. O report vai para a liderança com o nome do Scrum Master, e um destaque inventado vira compromisso que ele não assumiu. Cada decisão diz quem decide e até quando; decisão sem dono não acontece.

**status** é uma sugestão, e o Scrum Master confirma:
- **verde:** objetivo atingido, nenhum item acima do P85 e throughput ≥ P15 semanal;
- **amarelo:** objetivo parcial, OU 1-2 itens acima do P85, OU sistema instável;
- **vermelho:** objetivo não atingido E (itens críticos bloqueados OU previsão de 85% depois do compromisso).

**Previsões** sempre em probabilidade ("85% de chance até 14/11"), nunca uma data única. Uma data única é lida como promessa. Se `little.stable` for false ou `mc.zero_days_pct` for maior que 60, diga que a previsão tem confiança reduzida: o Monte Carlo supõe que o futuro se parece com o passado. Se houve `--growth`, diga na leitura qual crescimento foi considerado (o rodapé do card já registra). Se houver `type_spread` e a previsão não foi separada por tipo, diga isso como limitação.

**Sem métricas por pessoa.** O report mede o sistema de trabalho. Número individual vira meta pessoal e incentiva a otimizar o próprio indicador em vez do fluxo.

## 4. Gerar e conferir

```bash
python <pasta-da-skill>/scripts/flow_report.py render --out-dir out --narrative out/narrative.json
```

Confira antes de entregar: uma página, textos sem cortes, números da narrativa iguais aos do `metrics.json`. O report vai para a liderança; um texto cortado ou uma segunda página só aparece quando o Scrum Master já enviou.

Painéis de preview costumam não abrir `file://`. Use Chrome ou Edge em modo headless, que existe na maioria das máquinas (no Windows: `"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"`):

```bash
<navegador> --headless=new --disable-gpu --window-size=1400,1100 --screenshot=out/shot.png "file:///<caminho-absoluto>/report.html"
<navegador> --headless=new --disable-gpu --no-pdf-header-footer --print-to-pdf=out/check.pdf "file:///<caminho-absoluto>/report.html"
```

Olhe o screenshot e conte as páginas do PDF (deve ser 1). Se nenhum navegador estiver disponível, diga isso ao usuário e peça para ele conferir em "Imprimir / PDF".

Entregue o HTML; entregue o PDF se pedirem (A4 paisagem, com fundo). Para dúvidas de edição e impressão, ou para um report já editado, veja `references/edicao.md`.

## 5. Resposta ao Scrum Master

Curta, depois do arquivo:
- Status sugerido e o porquê em uma linha (ele confirma).
- Avisos de qualidade dos dados que afetam a conclusão.
- O que conferir antes de enviar.
- Uma ou duas perguntas poderosas para a Retrospectiva, ligadas ao que os dados mostraram (ex.: "O que fez ALFA-110 esperar 3 semanas sem ninguém puxar?").
- Um lembrete, em uma linha, de que os textos podem ser ajustados direto no navegador antes do envio.
- Se útil, a oferta de um rascunho curto de e-mail para os destinatários.

## 6. Análise rápida (sem sprint, sem report)

Para perguntas pontuais e para times Kanban. Mesmo CSV, mesmas regras de cálculo do report, então os números batem com os do One Page Report.

```bash
python <pasta-da-skill>/scripts/flow_report.py quick --csv EXPORT.csv [--dayfirst] [--as-of AAAA-MM-DD] \
  [--backlog 30] [--target-date AAAA-MM-DD] [--growth 1.1-1.4] \
  [--forecast-types "História"] [--backlog-by-type "História=20,Bug=10"] [--window-weeks 12] [--map campo="Coluna"]
```

- **`--as-of`** é o "hoje" da análise (fim da janela e base da idade dos itens). Sem ele, o script usa a data de hoje; se o export não tiver movimentação na última semana, usa a última data do CSV e avisa em `quality_notes`. Se o usuário disser quando exportou, passe essa data.
- A saída é um JSON com: percentis de Cycle Time, `throughput_week` (média, mediana, P15/P85 e as últimas 4 semanas), `delivered_last_7d`, `wip`, `aging` (todos os itens em andamento, do mais velho ao mais novo, com zona ok/atenção/crítico), `little`, `by_type`, `mc` e `quality_notes`. Formato de datas (`--dayfirst`), mapeamento de colunas e notas de qualidade seguem as regras da seção 2.
- Pule o que a pergunta não pede: sem `--backlog`/`--target-date`, não há Monte Carlo.

**Como responder.** Primeiro a resposta à pergunta, com o número e o "e daí?" em 2 a 5 frases. Depois, só se mudar a conclusão: avisos de qualidade, amostra pequena, sistema instável, dispersão entre tipos. As mesmas regras da seção 3 valem aqui: percentis e nunca média, previsão em probabilidade, nada por pessoa, nada inventado.

Exemplos de leitura:
- "O throughput caiu?" → compare as últimas 4 semanas com a mediana e com a faixa P15–P85 da janela. Abaixo do P15 é sinal; dentro da faixa é variação normal, e diga isso, porque tratar ruído como tendência leva a ações erradas.
- "O que está envelhecendo?" → itens na zona crítica (acima do P85), por ID, idade e coluna, com a pergunta para a daily: "o que falta para terminar este item?".
- "Quando terminamos?" → faixa 50/85/95, a premissa de crescimento usada (ou a falta dela) e o que pode mudar a previsão.

Se a pergunta pedir algo para enviar a alguém, ofereça o report completo em uma linha.
