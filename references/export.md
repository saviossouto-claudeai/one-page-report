# Como exportar o CSV

Leia este arquivo só quando o usuário perguntar como exportar, ou quando o `column_mapping` mostrar que falta uma coluna importante (sobretudo a data de início).

## Azure DevOps
1. Boards > Queries > New query.
2. Filtro sugerido: `Closed Date >= @Today-90 OR State <> Closed`, excluindo `State = Removed`.
3. Colunas: ID, Work Item Type, Title, State, Created Date, **Activated Date**, Closed Date, Iteration Path.
4. "Export to CSV".

Cuidado: a Activated Date é zerada quando o item volta para New. Nesses casos, o script usa a data de criação e avisa em `quality_notes`.
O estado "Resolved" costuma ser uma etapa intermediária: o script só considera o item concluído quando existe Closed Date.

## Jira
1. Filtro JQL, ex.: `project = ABC AND (resolved >= -90d OR statusCategory != Done)`.
2. Exportar > CSV (todos os campos).

O Jira não exporta nativamente a data em que o trabalho começou:
- **Ideal:** um campo customizado "Data de início", preenchido por automação na transição para Em andamento. Mapeie com `--map start="Data de início"`.
- **Sem esse campo:** o script usa "Status Category Changed" só para os itens em andamento. Nos itens concluídos, a conta parte da data de criação, e o Cycle Time vira, na prática, Lead Time. Deixe isso claro no report; o aviso aparece em `quality_notes`.

## Formato de datas
Abra o CSV e confira antes de rodar:
- `dd/mm/aaaa` ou `dd/mmm/aa` (Jira/ADO em pt-BR) → use `--dayfirst`.
- `m/d/aaaa` (ADO em inglês) → não use.
- ISO `aaaa-mm-dd` → tanto faz.
