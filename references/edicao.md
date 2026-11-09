# Edição e impressão do HTML gerado

Leia este arquivo quando o usuário perguntar como editar ou imprimir o report, ou quando ele trouxer um report já editado.

O HTML gerado é editável no navegador, sem precisar voltar ao Claude:
- Uma barra no topo (não sai na impressão) tem **Salvar versão editada**, que baixa `...-editado.html` e continua editável, e **Imprimir / PDF**.
- **Editáveis:** autor, status (texto e cor; clicar na bolinha alterna verde → amarelo → vermelho → cinza), objetivo e resultado da sprint, e todas as listas (leitura dos dados, destaques, riscos, decisões, próxima sprint). Enter cria um novo tópico; tópicos vazios não saem no PDF.
- **Não editáveis:** indicadores, gráficos, previsão e rodapé de método. Os números só mudam quando o report é regerado a partir do CSV ou dos parâmetros, para que ninguém ajuste um número à mão sem rastro.

## Impressão
A impressão sempre cabe em uma página A4 paisagem. O layout de impressão é fixo (não depende da largura da janela) e, antes de imprimir (botão ou Ctrl+P), a escala é recalculada conforme o tamanho do conteúdo editado. As cores, inclusive a bolinha de status, saem mesmo com "Gráficos de plano de fundo" desmarcado.
Oriente o usuário a escolher: Destino "Salvar como PDF", Layout Paisagem, Margens Padrão.

## Report já editado numa conversa nova
Se o usuário anexar um report editado e pedir ajustes de texto, edite os textos direto nesse HTML. Regerar apagaria as edições dele. Só regere se ele pedir números novos.
