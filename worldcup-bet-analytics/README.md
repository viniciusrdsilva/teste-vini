# World Cup Bet Analytics

Sistema em Python para analisar partidas de futebol e apontar **possiveis value bets** para odds da Bet365 ou de outro bookmaker.

Ele nao faz apostas automaticamente, nao garante lucro e deve ser usado apenas por maiores de idade, respeitando a legislacao local e os termos das APIs/bookmakers. A Bet365 nao oferece API publica direta para consumidores; por isso o projeto aceita odds via CSV ou por agregadores como The Odds API quando o bookmaker `bet365` estiver disponivel no plano/regiao.

## O que o sistema faz

- Busca partidas/resultados por CSV ou pela API publica do Football-Data.org.
- Busca odds por CSV ou pela The Odds API.
- Estima probabilidades 1X2 com um modelo Poisson simples baseado em historico de gols.
- Analisa mercados de vencedor, totais de gols, handicap, BTTS, dupla chance, draw-no-bet, escanteios, chutes, chutes no alvo e cartoes quando houver odds e dados historicos.
- Combina a previsao do modelo com consenso de mercado quando houver odds de outros bookmakers.
- Calcula probabilidade implicita, edge, EV por unidade apostada, odds minima justa e stake por Kelly fracionado.
- Gera relatorio no terminal, CSV e JSON.

## Instalacao

```bash
cd worldcup-bet-analytics
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

## Rodar demonstracao sem API

Os dados em `sample_data/` sao ficticios e servem apenas para testar o fluxo.

```bash
python -m bet365_value_finder demo
```

## Rodar com CSV manual da Bet365

Preencha um CSV com o mesmo formato de `sample_data/odds.csv` e rode:

```bash
python -m bet365_value_finder analyze \
  --fixtures-csv sample_data/fixtures.csv \
  --results-csv sample_data/results.csv \
  --odds-csv sample_data/odds.csv \
  --bookmaker bet365 \
  --bankroll 1000 \
  --output-csv reports/recommendations.csv \
  --output-json reports/recommendations.json
```

## Rodar com APIs

Configure `.env` com `FOOTBALL_DATA_API_KEY` e/ou `ODDS_API_KEY`.

```bash
python -m bet365_value_finder analyze \
  --use-football-data \
  --use-odds-api \
  --competition WC \
  --sport-key soccer_fifa_world_cup \
  --markets h2h,totals,spreads \
  --date-from 2026-06-18 \
  --date-to 2026-07-19 \
  --bookmaker bet365
```

Observacao: se o agregador nao retornar odds da Bet365 para o esporte/regiao/plano, use `--odds-csv` para informar odds manualmente ou altere `--bookmaker`.

## Mais mercados e foco em chance de acerto

Para incluir gols/handicap e ordenar por maior chance estimada:

```bash
python -m bet365_value_finder analyze \
  --use-football-data \
  --use-odds-api \
  --competition WC \
  --sport-key soccer_fifa_world_cup \
  --markets h2h,totals,spreads \
  --date-from 2026-06-18 \
  --date-to 2026-06-19 \
  --bookmaker pinnacle \
  --sort-by probability \
  --min-probability 0.55 \
  --include-all
```

Para tentar mercados extras por evento, como BTTS, dupla chance, escanteios, cartoes e props de chutes:

```bash
python -m bet365_value_finder analyze \
  --use-football-data \
  --use-odds-api \
  --competition WC \
  --sport-key soccer_fifa_world_cup \
  --markets h2h,totals,spreads \
  --event-markets btts,double_chance,draw_no_bet,alternate_totals_corners,alternate_spreads_corners,alternate_totals_cards,player_shots,player_shots_on_target \
  --date-from 2026-06-18 \
  --date-to 2026-06-19 \
  --bookmaker pinnacle \
  --sort-by probability
```

Escanteios, chutes, chutes no alvo e cartoes dependem de dados historicos especificos para modelagem. O app consegue listar/coletar odds desses mercados quando a API trouxer, mas so recomenda automaticamente quando houver `--stats-csv` com a metrica correspondente. Props de jogador, como `player_shots_on_target`, sao coletadas/listadas, mas nao recebem recomendacao automatica sem modelo de jogador.

## Top 30 da Copa por data

Use este comando para listar os 30 palpites mais provaveis dos jogos da Copa em uma data local especifica. Troque `DATA_AQUI` pela data desejada no formato `AAAA-MM-DD`.

```bash
cd /Users/viniciusrdsilva/Documents/teste-vini/worldcup-bet-analytics

DATA_AQUI=2026-06-18

python -m bet365_value_finder analyze \
  --use-football-data \
  --competition WC \
  --date-from "$DATA_AQUI" \
  --date-to "$(python -c "from datetime import date,timedelta; d=date.fromisoformat('$DATA_AQUI'); print((d+timedelta(days=1)).isoformat())")" \
  --local-date "$DATA_AQUI" \
  --timezone America/Sao_Paulo \
  --history-date-from 2026-06-11 \
  --history-date-to "$(python -c "from datetime import date,timedelta; d=date.fromisoformat('$DATA_AQUI'); print((d-timedelta(days=1)).isoformat())")" \
  --footystats-dir sample_data/footystats \
  --probabilities-only \
  --probability-metrics goals,corners,shots_on_target \
  --min-probability 0.0 \
  --limit 30 \
  --title "Copa - $DATA_AQUI - top 30 mais provaveis" \
  --output-csv "reports/worldcup_${DATA_AQUI}_top30_all_markets.csv" \
  --output-json "reports/worldcup_${DATA_AQUI}_top30_all_markets.json"
```

Exemplo para hoje, 18/06/2026:

############################################################################################################
############################################################################################################
############################################################################################################

```bash
cd /Users/viniciusrdsilva/Documents/teste-vini/worldcup-bet-analytics

DATA_AQUI=2026-06-18

python -m bet365_value_finder analyze \
  --use-football-data \
  --competition WC \
  --date-from "$DATA_AQUI" \
  --date-to "2026-06-19" \
  --local-date "$DATA_AQUI" \
  --timezone America/Sao_Paulo \
  --history-date-from 2026-06-11 \
  --history-date-to 2026-06-17 \
  --footystats-dir sample_data/footystats \
  --probabilities-only \
  --probability-metrics goals,corners,shots_on_target \
  --min-probability 0.0 \
  --limit 30 \
  --title "Copa - 2026-06-18 - top 30 mais provaveis" \
  --output-csv "reports/worldcup_2026-06-18_top30_all_markets.csv" \
  --output-json "reports/worldcup_2026-06-18_top30_all_markets.json"
```

Esse ranking mistura ganhadores, chances duplas, over/under gols, BTTS, escanteios e chutes no alvo. Para incluir tambem chutes totais e cartoes, use `--probability-metrics goals,corners,shots_on_target,shots,cards`.

############################################################################################################
############################################################################################################
############################################################################################################
## Formato dos CSVs

`fixtures.csv`

```csv
fixture_id,kickoff_utc,home_team,away_team,competition
demo-001,2026-06-20T18:00:00Z,Aurora FC,Boreal FC,DEMO
```

`results.csv`

```csv
kickoff_utc,home_team,away_team,home_goals,away_goals,competition
2026-05-01T18:00:00Z,Aurora FC,Boreal FC,2,0,DEMO
```

`odds.csv`

```csv
fixture_id,kickoff_utc,home_team,away_team,bookmaker,market,outcome,decimal_odds,last_update,point,description
demo-001,2026-06-20T18:00:00Z,Aurora FC,Boreal FC,bet365,h2h,home,1.95,2026-06-18T12:00:00Z,,
demo-001,2026-06-20T18:00:00Z,Aurora FC,Boreal FC,bet365,alternate_totals_corners,over,1.88,2026-06-18T12:00:00Z,9.5,
```

Para mercados de linha, adicione as colunas opcionais `point` e `description`.

`outcome` aceita `home`, `draw`, `away`, `over`, `under`, `yes`, `no` e combinacoes normalizadas como `home_draw`.

`stats.csv`

```csv
kickoff_utc,home_team,away_team,home_corners,away_corners,home_shots_on_target,away_shots_on_target,home_shots,away_shots,home_cards,away_cards,competition
2026-03-01T18:00:00Z,Aurora FC,Boreal FC,8,2,7,2,18,7,1,3,DEMO
```

Use com:

```bash
python -m bet365_value_finder analyze \
  --fixtures-csv sample_data/fixtures.csv \
  --results-csv sample_data/results.csv \
  --stats-csv sample_data/stats.csv \
  --odds-csv sample_data/odds.csv \
  --bookmaker bet365
```

## Interpretacao

- `model_prob`: probabilidade estimada pelo sistema.
- `decimal_odds`: odd oferecida.
- `implied_prob`: probabilidade implicita da odd, antes de remover margem.
- `edge`: retorno esperado por unidade apostada. `0.05` equivale a 5%.
- `fair_odds`: odd justa sem margem segundo o modelo.
- `min_odds_for_edge`: menor odd para bater o edge minimo configurado.
- `stake`: sugestao pelo Kelly fracionado com teto de risco.

Use as recomendacoes como triagem analitica, nao como ordem de aposta.
