from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .clients import (
    ApiError,
    FootballDataClient,
    OddsApiClient,
    load_footystats_dir,
    load_footystats_matches_csv,
    load_fixtures_csv,
    load_odds_csv,
    load_results_csv,
    load_stats_csv,
    load_stats_csv_url,
)
from .env import env_float, env_str, load_dotenv
from .matching import normalize_bookmaker
from .models import Fixture, MatchResult, OddsQuote
from .prediction import (
    PoissonGoalModel,
    blend_predictions,
    consensus_predictions_from_odds,
    enrich_predictions_with_stats,
)
from .recommendation import (
    attach_quotes_to_fixtures,
    build_pick_title,
    evaluate_market,
    recommend_value_bets,
)
from .reporting import (
    render_table,
    write_recommendations_csv,
    write_recommendations_json,
)
from .timeutils import date_end_utc, date_start_utc, iso_z, local_date, parse_date


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bet365-value-finder",
        description="Analisa odds de futebol e aponta possiveis value bets.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Rodar analise com CSVs e/ou APIs.")
    _add_common_args(analyze)

    demo = subparsers.add_parser("demo", help="Rodar demonstracao com dados ficticios.")
    demo.add_argument("--bankroll", type=float, default=env_float("BANKROLL", 1000.0))
    demo.add_argument("--min-edge", type=float, default=env_float("MIN_EDGE", 0.03))
    demo.add_argument("--include-all", action="store_true")
    return parser


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    today = date.today()
    parser.add_argument("--fixtures-csv", type=Path)
    parser.add_argument("--results-csv", type=Path)
    stats_csv_default = env_str("STATS_CSV", "")
    parser.add_argument("--stats-csv", type=Path, default=Path(stats_csv_default) if stats_csv_default else None)
    parser.add_argument("--footystats-matches-csv", type=Path)
    parser.add_argument(
        "--footystats-dir",
        type=Path,
        default=Path(env_str("FOOTYSTATS_DIR", "")) if env_str("FOOTYSTATS_DIR", "") else None,
        help="Pasta com CSVs exportados da FootyStats.",
    )
    parser.add_argument(
        "--stats-url",
        default=env_str("STATS_URL", ""),
        help="URL de um CSV de estatisticas no formato stats.csv.",
    )
    parser.add_argument("--odds-csv", type=Path)
    parser.add_argument("--use-football-data", action="store_true")
    parser.add_argument("--use-odds-api", action="store_true")
    parser.add_argument("--football-data-key", default=os.getenv("FOOTBALL_DATA_API_KEY", ""))
    parser.add_argument("--odds-api-key", default=os.getenv("ODDS_API_KEY", ""))
    parser.add_argument(
        "--competition",
        default=env_str("FOOTBALL_DATA_COMPETITION", "WC"),
        help="Codigo da competicao no Football-Data.org, ex.: WC, PL, BL1.",
    )
    parser.add_argument(
        "--sport-key",
        default=env_str("ODDS_API_SPORT_KEY", "soccer_fifa_world_cup"),
        help="Sport key da The Odds API.",
    )
    parser.add_argument("--regions", default=env_str("ODDS_API_REGIONS", "eu"))
    parser.add_argument(
        "--markets",
        default=env_str("ODDS_API_MARKETS", "h2h"),
        help="Mercados do endpoint principal da Odds API. Ex.: h2h,totals,spreads.",
    )
    parser.add_argument(
        "--event-markets",
        default=env_str("ODDS_API_EVENT_MARKETS", ""),
        help=(
            "Mercados por evento da Odds API. Ex.: btts,double_chance,"
            "alternate_totals_corners. Consome chamadas por jogo."
        ),
    )
    parser.add_argument(
        "--odds-api-only-bookmaker",
        action="store_true",
        help="Consulta somente o bookmaker alvo na The Odds API. Economiza payload, mas perde consenso de mercado.",
    )
    parser.add_argument("--date-from", default=today.isoformat())
    parser.add_argument("--date-to", default=(today + timedelta(days=14)).isoformat())
    parser.add_argument(
        "--local-date",
        default=env_str("LOCAL_DATE", ""),
        help="Filtra partidas pelo dia local, ex.: 2026-06-18.",
    )
    parser.add_argument(
        "--timezone",
        default=env_str("TIMEZONE", "America/Sao_Paulo"),
        help="Fuso usado por --local-date.",
    )
    parser.add_argument("--history-date-from", default=(today - timedelta(days=365)).isoformat())
    parser.add_argument("--history-date-to", default=(today - timedelta(days=1)).isoformat())
    parser.add_argument("--bookmaker", default=env_str("TARGET_BOOKMAKER", "bet365"))
    parser.add_argument("--bankroll", type=float, default=env_float("BANKROLL", 1000.0))
    parser.add_argument("--min-edge", type=float, default=env_float("MIN_EDGE", 0.03))
    parser.add_argument("--kelly-fraction", type=float, default=env_float("KELLY_FRACTION", 0.25))
    parser.add_argument("--max-stake-pct", type=float, default=env_float("MAX_STAKE_PCT", 0.02))
    parser.add_argument("--model-weight", type=float, default=env_float("MODEL_WEIGHT", 0.65))
    parser.add_argument("--min-probability", type=float, default=env_float("MIN_PROBABILITY", 0.0))
    parser.add_argument(
        "--sort-by",
        choices=["edge", "probability", "stake"],
        default=env_str("SORT_BY", "edge"),
        help="Ordenacao do relatorio: valor esperado, chance de acerto ou stake.",
    )
    parser.add_argument("--include-all", action="store_true")
    parser.add_argument(
        "--probabilities-only",
        action="store_true",
        help="Mostra ranking de probabilidades do modelo mesmo sem odds.",
    )
    parser.add_argument(
        "--probability-metrics",
        default=env_str("PROBABILITY_METRICS", ""),
        help="Filtra ranking sem odds por metricas. Ex.: corners,shots_on_target.",
    )
    parser.add_argument(
        "--list-bookmakers",
        action="store_true",
        help="Lista bookmakers encontrados nas odds e encerra sem calcular recomendacoes.",
    )
    parser.add_argument(
        "--list-markets",
        action="store_true",
        help="Lista mercados encontrados nas odds e encerra sem calcular recomendacoes.",
    )
    parser.add_argument("--title", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output-csv", type=Path, default=Path("reports/recommendations.csv"))
    parser.add_argument("--output-json", type=Path)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path.cwd() / ".env")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "demo":
        return run_demo(args)
    if args.command == "analyze":
        return run_analysis(args)
    parser.error("Comando invalido.")
    return 2


def run_demo(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[1]
    demo_args = argparse.Namespace(
        fixtures_csv=root / "sample_data" / "fixtures.csv",
        results_csv=root / "sample_data" / "results.csv",
        stats_csv=root / "sample_data" / "stats.csv",
        footystats_matches_csv=None,
        footystats_dir=None,
        stats_url="",
        odds_csv=root / "sample_data" / "odds.csv",
        use_football_data=False,
        use_odds_api=False,
        football_data_key="",
        odds_api_key="",
        competition="DEMO",
        sport_key="",
        regions="",
        markets="h2h",
        event_markets="",
        odds_api_only_bookmaker=False,
        date_from=date.today().isoformat(),
        date_to=(date.today() + timedelta(days=14)).isoformat(),
        local_date="",
        timezone=env_str("TIMEZONE", "America/Sao_Paulo"),
        history_date_from=(date.today() - timedelta(days=365)).isoformat(),
        history_date_to=(date.today() - timedelta(days=1)).isoformat(),
        bookmaker="bet365",
        bankroll=args.bankroll,
        min_edge=args.min_edge,
        kelly_fraction=env_float("KELLY_FRACTION", 0.25),
        max_stake_pct=env_float("MAX_STAKE_PCT", 0.02),
        model_weight=env_float("MODEL_WEIGHT", 0.65),
        min_probability=env_float("MIN_PROBABILITY", 0.0),
        sort_by=env_str("SORT_BY", "edge"),
        include_all=args.include_all,
        probabilities_only=False,
        probability_metrics="",
        list_bookmakers=False,
        list_markets=False,
        limit=20,
        title="Demo - apostas com maior valor esperado",
        output_csv=root / "reports" / "demo_recommendations.csv",
        output_json=root / "reports" / "demo_recommendations.json",
    )
    print("Rodando demo com dados ficticios em sample_data/.\n")
    return run_analysis(demo_args)


def run_analysis(args: argparse.Namespace) -> int:
    date_from = _required_date(args.date_from, "--date-from")
    date_to = _required_date(args.date_to, "--date-to")
    requested_local_date = parse_date(args.local_date) if args.local_date else None
    history_date_from = _required_date(args.history_date_from, "--history-date-from")
    history_date_to = _required_date(args.history_date_to, "--history-date-to")

    fixtures: list[Fixture] = []
    results: list[MatchResult] = []
    stats = []
    quotes: list[OddsQuote] = []
    api_errors: list[str] = []

    if args.fixtures_csv:
        fixtures.extend(load_fixtures_csv(args.fixtures_csv))
    if args.results_csv:
        results.extend(load_results_csv(args.results_csv))
    if args.stats_csv:
        stats.extend(load_stats_csv(args.stats_csv))
    if args.footystats_matches_csv:
        stats.extend(load_footystats_matches_csv(args.footystats_matches_csv))
    if args.footystats_dir:
        stats.extend(load_footystats_dir(args.footystats_dir))
    if args.stats_url:
        try:
            stats.extend(load_stats_csv_url(args.stats_url))
        except ApiError as exc:
            api_errors.append(f"Stats CSV URL: {exc}")
    if args.odds_csv:
        quotes.extend(load_odds_csv(args.odds_csv))

    if args.use_football_data:
        try:
            if not args.football_data_key:
                raise ApiError("FOOTBALL_DATA_API_KEY nao configurada.")
            football_client = FootballDataClient(args.football_data_key)
            api_fixtures, api_results = football_client.competition_matches(
                args.competition,
                date_from,
                date_to,
            )
            fixtures.extend(api_fixtures)
            results.extend(api_results)
            _, history_results = football_client.competition_matches(
                args.competition,
                history_date_from,
                history_date_to,
                status="FINISHED",
            )
            results.extend(history_results)
        except ApiError as exc:
            api_errors.append(f"Football-Data.org: {exc}")

    if args.use_odds_api:
        try:
            if not args.odds_api_key:
                raise ApiError("ODDS_API_KEY nao configurada.")
            odds_client = OddsApiClient(args.odds_api_key)
            quotes.extend(
                odds_client.odds(
                    sport_key=args.sport_key,
                    regions=args.regions,
                    markets=args.markets,
                    bookmaker=args.bookmaker if args.odds_api_only_bookmaker else None,
                    commence_from=date_start_utc(date_from),
                    commence_to=date_end_utc(date_to),
                )
            )
            if args.event_markets:
                event_ids = sorted({quote.event_id for quote in quotes if quote.event_id})
                if not event_ids:
                    api_events = odds_client.events(
                        sport_key=args.sport_key,
                        commence_from=date_start_utc(date_from),
                        commence_to=date_end_utc(date_to),
                    )
                    fixtures.extend(api_events)
                    event_ids = [fixture.fixture_id for fixture in api_events]
                for event_id in event_ids:
                    quotes.extend(
                        odds_client.event_odds(
                            sport_key=args.sport_key,
                            event_id=event_id,
                            regions=args.regions,
                            markets=args.event_markets,
                            bookmaker=args.bookmaker if args.odds_api_only_bookmaker else None,
                        )
                    )
        except ApiError as exc:
            api_errors.append(f"The Odds API: {exc}")

    for error in api_errors:
        print(f"Aviso de API: {error}")
    if api_errors:
        print("Continuando com as fontes que retornaram dados.\n")

    if not fixtures and quotes:
        fixtures = fixtures_from_quotes(quotes)

    fixtures = dedupe_fixtures(fixtures)
    if requested_local_date:
        fixtures = [
            fixture
            for fixture in fixtures
            if local_date(fixture.kickoff_utc, args.timezone) == requested_local_date
        ]
    if not fixtures:
        print("Nenhuma partida encontrada. Informe --fixtures-csv, --use-football-data ou --use-odds-api.")
        return 1

    model = PoissonGoalModel().fit(results)
    model_predictions = {
        fixture.fixture_id: model.predict(fixture)
        for fixture in fixtures
    }
    model_predictions = enrich_predictions_with_stats(model_predictions, fixtures, stats)
    if not quotes and (args.list_bookmakers or args.list_markets):
        print("Nenhuma odd encontrada. Nao ha bookmakers ou mercados para listar.")
        return 0
    if args.probabilities_only or not quotes:
        if not quotes:
            print(
                "Nenhuma odd encontrada. Gerando ranking de probabilidades sem odds; "
                "isso nao calcula EV, stake ou valor de aposta.\n"
            )
        title = args.title or "Maiores probabilidades do modelo"
        probability_picks = build_probability_picks(
            fixtures=fixtures,
            predictions=model_predictions,
            min_probability=args.min_probability,
            metrics=parse_metric_filter(args.probability_metrics),
        )[: args.limit]
        print(
            render_probability_table(
                picks=probability_picks,
                title=title,
            )
        )
        if args.output_csv:
            write_probability_csv(args.output_csv, probability_picks)
            print(f"\nCSV salvo em: {args.output_csv}")
        if args.output_json:
            write_probability_json(args.output_json, probability_picks)
            print(f"JSON salvo em: {args.output_json}")
        return 0

    quotes_by_fixture = attach_quotes_to_fixtures(fixtures, quotes)
    if args.list_bookmakers:
        print_bookmaker_summary(quotes_by_fixture)
        return 0
    if args.list_markets:
        print_market_summary(quotes_by_fixture)
        return 0

    consensus_predictions = consensus_predictions_from_odds(
        fixtures,
        quotes_by_fixture,
        excluded_bookmaker=args.bookmaker,
    )
    predictions = blend_predictions(
        model_predictions,
        consensus_predictions,
        model_weight=args.model_weight,
    )
    recommendations = recommend_value_bets(
        fixtures=fixtures,
        predictions=predictions,
        quotes_by_fixture=quotes_by_fixture,
        bookmaker=args.bookmaker,
        bankroll=args.bankroll,
        min_edge=args.min_edge,
        kelly_fraction=args.kelly_fraction,
        max_stake_pct=args.max_stake_pct,
        include_all=args.include_all,
        min_probability=args.min_probability,
        sort_by=args.sort_by,
    )

    target_bookmaker = normalize_bookmaker(args.bookmaker)
    target_quote_count = sum(
        1
        for grouped_quotes in quotes_by_fixture.values()
        for quote in grouped_quotes
        if quote.bookmaker_key == target_bookmaker
    )
    print(
        f"Partidas: {len(fixtures)} | resultados historicos: {len(results)} | "
        f"odds vinculadas: {sum(len(v) for v in quotes_by_fixture.values())} | "
        f"odds {args.bookmaker}: {target_quote_count}"
    )
    print()
    title = args.title or _default_report_title(args)
    print(render_table(recommendations, limit=args.limit, title=title))

    if args.output_csv:
        write_recommendations_csv(args.output_csv, recommendations)
        print(f"\nCSV salvo em: {args.output_csv}")
    if args.output_json:
        write_recommendations_json(args.output_json, recommendations)
        print(f"JSON salvo em: {args.output_json}")
    if target_quote_count == 0:
        print(
            "\nAviso: nenhuma odd do bookmaker alvo foi encontrada. "
            "Verifique o nome do bookmaker, a regiao/plano do agregador ou use --odds-csv."
        )
        print()
        print_bookmaker_summary(quotes_by_fixture)
        print()
        print_market_summary(quotes_by_fixture)
    return 0


def fixtures_from_quotes(quotes: list[OddsQuote]) -> list[Fixture]:
    fixtures: dict[str, Fixture] = {}
    for quote in quotes:
        fixture_id = quote.event_id or (
            f"{quote.home_team}-{quote.away_team}-{quote.kickoff_utc or ''}"
        )
        if fixture_id in fixtures:
            continue
        fixtures[fixture_id] = Fixture(
            fixture_id=fixture_id,
            kickoff_utc=quote.kickoff_utc,
            home_team=quote.home_team,
            away_team=quote.away_team,
            competition="odds-api",
        )
    return list(fixtures.values())


def dedupe_fixtures(fixtures: list[Fixture]) -> list[Fixture]:
    deduped: dict[str, Fixture] = {}
    for fixture in fixtures:
        fixture_id = fixture.fixture_id or (
            f"{fixture.home_team}-{fixture.away_team}-{fixture.kickoff_utc or ''}"
        )
        if fixture_id not in deduped:
            deduped[fixture_id] = Fixture(
                fixture_id=fixture_id,
                kickoff_utc=fixture.kickoff_utc,
                home_team=fixture.home_team,
                away_team=fixture.away_team,
                competition=fixture.competition,
            )
    return list(deduped.values())


def print_bookmaker_summary(quotes_by_fixture: dict[str, list[OddsQuote]]) -> None:
    counts = Counter(
        (quote.bookmaker_key, quote.bookmaker_title)
        for grouped_quotes in quotes_by_fixture.values()
        for quote in grouped_quotes
    )
    if not counts:
        print("Nenhum bookmaker encontrado nas odds vinculadas aos jogos.")
        return
    print("Bookmakers disponiveis nas odds vinculadas:")
    for (bookmaker_key, bookmaker_title), count in counts.most_common():
        print(f"- {bookmaker_key} ({bookmaker_title}): {count} odds")


def print_market_summary(quotes_by_fixture: dict[str, list[OddsQuote]]) -> None:
    counts = Counter(
        quote.market
        for grouped_quotes in quotes_by_fixture.values()
        for quote in grouped_quotes
    )
    if not counts:
        print("Nenhum mercado encontrado nas odds vinculadas aos jogos.")
        return
    print("Mercados disponiveis nas odds vinculadas:")
    for market, count in counts.most_common():
        print(f"- {market}: {count} odds")


def _default_report_title(args: argparse.Namespace) -> str:
    if args.sort_by == "probability":
        return "Melhores apostas por chance de acerto"
    if args.sort_by == "stake":
        return "Melhores apostas por stake sugerida"
    return "Melhores apostas por valor esperado"


@dataclass(frozen=True)
class ProbabilityPick:
    fixture: Fixture
    title: str
    market: str
    metric: str
    probability: float
    expected_label: str
    confidence: str


def render_probability_table(
    picks: list[ProbabilityPick],
    title: str,
) -> str:
    if not picks:
        return f"{title}\n\nNenhum palpite atingiu a probabilidade minima configurada."

    headers = ["Jogo", "Titulo", "Mercado", "Prob", "Expectativa", "Conf."]
    rows = [
        [
            f"{pick.fixture.home_team} x {pick.fixture.away_team}",
            pick.title,
            pick.market,
            f"{pick.probability:.1%}",
            pick.expected_label,
            pick.confidence,
        ]
        for pick in picks
    ]
    widths = [
        max(len(str(row[index])) for row in [headers, *rows])
        for index in range(len(headers))
    ]

    def format_row(row: list[str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))

    divider = "-+-".join("-" * width for width in widths)
    return "\n".join([title, "", format_row(headers), divider, *[format_row(row) for row in rows]])


def write_probability_csv(path: Path, picks: list[ProbabilityPick]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [probability_pick_to_dict(pick) for pick in picks]
    fieldnames = [
        "fixture_id",
        "kickoff_utc",
        "home_team",
        "away_team",
        "title",
        "market",
        "metric",
        "probability",
        "expected_label",
        "confidence",
    ]
    with path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_probability_json(path: Path, picks: list[ProbabilityPick]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [probability_pick_to_dict(pick) for pick in picks],
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )


def probability_pick_to_dict(pick: ProbabilityPick) -> dict[str, object]:
    return {
        "fixture_id": pick.fixture.fixture_id,
        "kickoff_utc": iso_z(pick.fixture.kickoff_utc),
        "home_team": pick.fixture.home_team,
        "away_team": pick.fixture.away_team,
        "title": pick.title,
        "market": pick.market,
        "metric": pick.metric,
        "probability": pick.probability,
        "expected_label": pick.expected_label,
        "confidence": pick.confidence,
    }


def build_probability_picks(
    fixtures: list[Fixture],
    predictions: dict[str, object],
    min_probability: float,
    metrics: set[str] | None = None,
) -> list[ProbabilityPick]:
    picks: list[ProbabilityPick] = []
    for fixture in fixtures:
        prediction = predictions.get(fixture.fixture_id)
        if prediction is None:
            continue
        for quote in _probability_candidate_quotes(fixture):
            evaluation = evaluate_market(prediction, quote)  # type: ignore[arg-type]
            metric = _quote_metric(quote)
            if metrics and metric not in metrics:
                continue
            if evaluation is None or evaluation.win_prob < min_probability:
                continue
            picks.append(
                ProbabilityPick(
                    fixture=fixture,
                    title=build_pick_title(fixture, quote),
                    market=_market_label(quote.market, quote.point),
                    metric=metric,
                    probability=evaluation.win_prob,
                    expected_label=_probability_expected_label(prediction, quote),  # type: ignore[arg-type]
                    confidence=_prediction_confidence(prediction.evidence_matches),  # type: ignore[attr-defined]
                )
            )
    picks.sort(key=lambda item: item.probability, reverse=True)
    return picks


def _probability_candidate_quotes(fixture: Fixture) -> list[OddsQuote]:
    quotes: list[OddsQuote] = []
    for market, outcomes, points in [
        ("h2h", ["home", "draw", "away"], [None]),
        ("double_chance", ["home_draw", "home_away", "draw_away"], [None]),
        ("btts", ["yes", "no"], [None]),
        ("totals", ["over", "under"], [1.5, 2.5, 3.5]),
    ]:
        for outcome in outcomes:
            for point in points:
                quotes.append(
                    OddsQuote(
                        event_id=fixture.fixture_id,
                        fixture_id=fixture.fixture_id,
                        kickoff_utc=fixture.kickoff_utc,
                        home_team=fixture.home_team,
                        away_team=fixture.away_team,
                        bookmaker_key="model",
                        bookmaker_title="Modelo",
                        market=market,
                        outcome=outcome,
                        decimal_odds=1.0,
                        point=point,
                    )
                )
    for metric, market, points in [
        ("corners", "alternate_totals_corners", [7.5, 8.5, 9.5, 10.5]),
        ("shots_on_target", "totals_shots_on_target", [6.5, 7.5, 8.5, 9.5]),
        ("shots", "totals_shots", [20.5, 22.5, 24.5, 26.5]),
        ("cards", "alternate_totals_cards", [3.5, 4.5, 5.5, 6.5]),
    ]:
        for outcome in ["over", "under"]:
            for point in points:
                quotes.append(
                    OddsQuote(
                        event_id=fixture.fixture_id,
                        fixture_id=fixture.fixture_id,
                        kickoff_utc=fixture.kickoff_utc,
                        home_team=fixture.home_team,
                        away_team=fixture.away_team,
                        bookmaker_key="model",
                        bookmaker_title="Modelo",
                        market=market,
                        outcome=outcome,
                        decimal_odds=1.0,
                        point=point,
                        description=metric,
                    )
                )
    return quotes


def _market_label(market: str, point: float | None) -> str:
    if point is None:
        return market
    return f"{market} {point:g}"


def _prediction_confidence(evidence_matches: int) -> str:
    if evidence_matches >= 16:
        return "media"
    if evidence_matches >= 8:
        return "baixa-media"
    return "baixa"


def _probability_expected_label(prediction, quote: OddsQuote) -> str:
    metric = _quote_metric(quote)
    if metric == "goals":
        return f"{prediction.expected_home_goals:.2f}-{prediction.expected_away_goals:.2f} gols"
    expected_home, expected_away = prediction.expected_metrics.get(metric, (0.0, 0.0))
    label = {
        "corners": "esc.",
        "shots_on_target": "SOT",
        "shots": "chutes",
        "cards": "cart.",
    }.get(metric, metric)
    return f"{expected_home:.2f}-{expected_away:.2f} {label}"


def _quote_metric(quote: OddsQuote) -> str:
    market = quote.market
    if "corner" in market:
        return "corners"
    if "shots_on_target" in market:
        return "shots_on_target"
    if "shots" in market:
        return "shots"
    if "card" in market or "booking" in market:
        return "cards"
    return "goals"


def parse_metric_filter(value: str) -> set[str] | None:
    if not value.strip():
        return None
    aliases = {
        "escanteio": "corners",
        "escanteios": "corners",
        "corner": "corners",
        "corners": "corners",
        "chute_no_alvo": "shots_on_target",
        "chutes_no_alvo": "shots_on_target",
        "shot_on_target": "shots_on_target",
        "shots_on_target": "shots_on_target",
        "sot": "shots_on_target",
        "chute": "shots",
        "chutes": "shots",
        "shots": "shots",
        "cartao": "cards",
        "cartoes": "cards",
        "card": "cards",
        "cards": "cards",
        "gol": "goals",
        "gols": "goals",
        "goal": "goals",
        "goals": "goals",
    }
    metrics = set()
    for raw_item in value.split(","):
        item = raw_item.strip().lower().replace(" ", "_").replace("-", "_")
        if item:
            metrics.add(aliases.get(item, item))
    return metrics


def _required_date(value: str, name: str) -> date:
    parsed = parse_date(value)
    if parsed is None:
        raise SystemExit(f"{name} precisa ser uma data ISO, ex.: 2026-06-18")
    return parsed
