from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from .matching import find_fixture_for_quote, normalize_bookmaker, normalize_name
from .models import Fixture, OddsQuote, Prediction, Recommendation


@dataclass(frozen=True)
class MarketEvaluation:
    win_prob: float
    push_prob: float = 0.0

    @property
    def lose_prob(self) -> float:
        return max(0.0, 1.0 - self.win_prob - self.push_prob)


def attach_quotes_to_fixtures(
    fixtures: list[Fixture],
    quotes: list[OddsQuote],
) -> dict[str, list[OddsQuote]]:
    grouped: dict[str, list[OddsQuote]] = defaultdict(list)
    for quote in quotes:
        fixture = find_fixture_for_quote(quote, fixtures)
        if fixture is None:
            continue
        grouped[fixture.fixture_id].append(quote)
    return dict(grouped)


def recommend_value_bets(
    fixtures: list[Fixture],
    predictions: dict[str, Prediction],
    quotes_by_fixture: dict[str, list[OddsQuote]],
    bookmaker: str,
    bankroll: float,
    min_edge: float,
    kelly_fraction: float,
    max_stake_pct: float,
    include_all: bool = False,
    min_probability: float = 0.0,
    sort_by: str = "edge",
) -> list[Recommendation]:
    target_bookmaker = normalize_bookmaker(bookmaker)
    fixture_by_id = {fixture.fixture_id: fixture for fixture in fixtures}
    recommendations: list[Recommendation] = []

    for fixture_id, prediction in predictions.items():
        fixture = fixture_by_id.get(fixture_id)
        if fixture is None:
            continue
        for quote in quotes_by_fixture.get(fixture_id, []):
            if quote.bookmaker_key != target_bookmaker:
                continue
            evaluation = evaluate_market(prediction, quote)
            if evaluation is None:
                continue
            probability = evaluation.win_prob
            if probability < min_probability:
                continue
            implied_probability = 1.0 / quote.decimal_odds
            edge = _expected_value(evaluation, quote.decimal_odds)
            if edge < min_edge and not include_all:
                continue
            fair_odds = _fair_odds(evaluation)
            min_odds_for_edge = _min_odds_for_edge(evaluation, min_edge)
            raw_kelly = _kelly_fraction(evaluation, quote.decimal_odds)
            capped_fraction = min(max_stake_pct, max(0.0, raw_kelly * kelly_fraction))
            stake = bankroll * capped_fraction
            expected_metric = _metric_for_quote(quote) or "goals"
            expected_metric_home, expected_metric_away = _expected_metric_values(
                prediction,
                expected_metric,
            )
            recommendations.append(
                Recommendation(
                    title=build_pick_title(fixture, quote),
                    fixture_id=fixture.fixture_id,
                    kickoff_utc=fixture.kickoff_utc,
                    home_team=fixture.home_team,
                    away_team=fixture.away_team,
                    bookmaker=quote.bookmaker_title,
                    market=quote.market,
                    outcome=quote.outcome,
                    point=quote.point,
                    decimal_odds=quote.decimal_odds,
                    model_prob=probability,
                    push_prob=evaluation.push_prob,
                    implied_prob=implied_probability,
                    fair_odds=fair_odds,
                    min_odds_for_edge=min_odds_for_edge,
                    edge=edge,
                    kelly_fraction=capped_fraction,
                    stake=stake,
                    expected_home_goals=prediction.expected_home_goals,
                    expected_away_goals=prediction.expected_away_goals,
                    expected_metric=expected_metric,
                    expected_metric_home=expected_metric_home,
                    expected_metric_away=expected_metric_away,
                    confidence=_confidence(prediction),
                    reason=_reason(edge, probability, quote.decimal_odds, min_edge),
                )
            )

    recommendations.sort(key=_sort_key(sort_by), reverse=True)
    return recommendations


def evaluate_market(prediction: Prediction, quote: OddsQuote) -> MarketEvaluation | None:
    market = quote.market
    if market in {"h2h", "h2h_3_way"} and quote.outcome in {"home", "draw", "away"}:
        return MarketEvaluation(prediction.probability_for(quote.outcome))

    if market == "draw_no_bet" and quote.outcome in {"home", "away"}:
        win = prediction.p_home if quote.outcome == "home" else prediction.p_away
        return MarketEvaluation(win_prob=win, push_prob=prediction.p_draw)

    if market == "double_chance":
        mapping = {
            "home_draw": prediction.p_home + prediction.p_draw,
            "home_away": prediction.p_home + prediction.p_away,
            "draw_away": prediction.p_draw + prediction.p_away,
        }
        if quote.outcome in mapping:
            return MarketEvaluation(mapping[quote.outcome])

    if market == "btts":
        yes_probability = _btts_probability(prediction)
        if quote.outcome == "yes":
            return MarketEvaluation(yes_probability)
        if quote.outcome == "no":
            return MarketEvaluation(1.0 - yes_probability)

    if _is_goal_total_market(market):
        if quote.point is None or quote.outcome not in {"over", "under"}:
            return None
        return _total_goals_evaluation(prediction, quote.outcome, quote.point)

    metric = _metric_for_quote(quote)
    if metric and _is_metric_total_market(market):
        if quote.point is None or quote.outcome not in {"over", "under"}:
            return None
        return _metric_total_evaluation(prediction, metric, quote.outcome, quote.point)

    if _is_goal_spread_market(market):
        if quote.point is None or quote.outcome not in {"home", "away"}:
            return None
        return _spread_evaluation(prediction, quote.outcome, quote.point)

    if metric and _is_metric_spread_market(market):
        if quote.point is None or quote.outcome not in {"home", "away"}:
            return None
        return _metric_spread_evaluation(prediction, metric, quote.outcome, quote.point)

    if market in {"team_totals", "alternate_team_totals"}:
        if quote.point is None or quote.outcome not in {"over", "under"}:
            return None
        return _team_total_evaluation(prediction, quote)

    if metric and _is_metric_team_total_market(market):
        if quote.point is None or quote.outcome not in {"over", "under"}:
            return None
        return _metric_team_total_evaluation(prediction, metric, quote)

    return None


def build_pick_title(fixture: Fixture, quote: OddsQuote) -> str:
    if quote.market in {"h2h", "h2h_3_way"}:
        if quote.outcome == "home":
            return f"{fixture.home_team} vence"
        if quote.outcome == "away":
            return f"{fixture.away_team} vence"
        if quote.outcome == "draw":
            return "Empate"
    if quote.market == "draw_no_bet":
        team = fixture.home_team if quote.outcome == "home" else fixture.away_team
        return f"{team} empate anula"
    if quote.market == "double_chance":
        labels = {
            "home_draw": f"{fixture.home_team} ou empate",
            "home_away": f"{fixture.home_team} ou {fixture.away_team}",
            "draw_away": f"Empate ou {fixture.away_team}",
        }
        return labels.get(quote.outcome, quote.outcome)
    if quote.market == "btts":
        return "Ambos marcam - Sim" if quote.outcome == "yes" else "Ambos marcam - Nao"
    if _is_goal_total_market(quote.market):
        return f"{quote.outcome.title()} {quote.point:g} gols"
    metric = _metric_for_quote(quote)
    if metric and _is_metric_total_market(quote.market):
        return f"{quote.outcome.title()} {quote.point:g} {_metric_label(metric)}"
    if _is_goal_spread_market(quote.market):
        team = fixture.home_team if quote.outcome == "home" else fixture.away_team
        return f"{team} handicap {quote.point:+g}"
    if metric and _is_metric_spread_market(quote.market):
        team = fixture.home_team if quote.outcome == "home" else fixture.away_team
        return f"{team} handicap {_metric_label(metric)} {quote.point:+g}"
    if quote.market in {"team_totals", "alternate_team_totals"}:
        target = quote.description or "Time"
        return f"{target} {quote.outcome.title()} {quote.point:g} gols"
    if metric and _is_metric_team_total_market(quote.market):
        target = quote.description or "Time"
        return f"{target} {quote.outcome.title()} {quote.point:g} {_metric_label(metric)}"
    if quote.market in {"player_shots", "player_shots_on_target"}:
        target = quote.description or "Jogador"
        return f"{target} {quote.outcome.title()} {quote.point:g} {_metric_label(metric or quote.market)}"
    if quote.market.startswith("player_goal_scorer"):
        target = quote.description or "Jogador"
        return f"{target} marca gol"
    if quote.market.startswith("player_to_receive"):
        target = quote.description or "Jogador"
        return f"{target} recebe cartao"
    return f"{quote.market} {quote.outcome}"


def _expected_value(evaluation: MarketEvaluation, decimal_odds: float) -> float:
    return (evaluation.win_prob * (decimal_odds - 1.0)) - evaluation.lose_prob


def _fair_odds(evaluation: MarketEvaluation) -> float:
    if evaluation.win_prob <= 0:
        return float("inf")
    return 1.0 + (evaluation.lose_prob / evaluation.win_prob)


def _min_odds_for_edge(evaluation: MarketEvaluation, min_edge: float) -> float:
    if evaluation.win_prob <= 0:
        return float("inf")
    return 1.0 + ((evaluation.lose_prob + min_edge) / evaluation.win_prob)


def _kelly_fraction(evaluation: MarketEvaluation, decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        return 0.0
    net_odds = decimal_odds - 1.0
    denominator = net_odds * (evaluation.win_prob + evaluation.lose_prob)
    if denominator <= 0:
        return 0.0
    return ((net_odds * evaluation.win_prob) - evaluation.lose_prob) / denominator


def _is_goal_total_market(market: str) -> bool:
    if "corner" in market or "card" in market or "booking" in market or "shot" in market:
        return False
    return market in {"totals", "alternate_totals"} or market.startswith("totals_")


def _is_goal_spread_market(market: str) -> bool:
    if "corner" in market or "card" in market or "booking" in market or "shot" in market:
        return False
    return market in {"spreads", "alternate_spreads"} or market.startswith("spreads_")


def _metric_for_quote(quote: OddsQuote) -> str | None:
    market = quote.market.lower()
    description = normalize_name(quote.description)
    if "corner" in market or "escanteio" in description:
        return "corners"
    if "shot_on_target" in market or "shots_on_target" in market:
        return "shots_on_target"
    if "shot" in market or "chute" in description:
        return "shots"
    if "card" in market or "booking" in market or "cartao" in description:
        return "cards"
    return None


def _metric_label(metric: str) -> str:
    return {
        "corners": "escanteios",
        "shots_on_target": "chutes no alvo",
        "shots": "chutes",
        "cards": "cartoes",
        "player_shots": "chutes",
        "player_shots_on_target": "chutes no alvo",
    }.get(metric, metric)


def _expected_metric_values(prediction: Prediction, metric: str) -> tuple[float, float]:
    if metric == "goals":
        return prediction.expected_home_goals, prediction.expected_away_goals
    return prediction.expected_metrics.get(metric, (0.0, 0.0))


def _is_metric_total_market(market: str) -> bool:
    return "totals" in market and ("corner" in market or "card" in market or "shot" in market)


def _is_metric_spread_market(market: str) -> bool:
    return "spreads" in market and ("corner" in market or "card" in market or "shot" in market)


def _is_metric_team_total_market(market: str) -> bool:
    return "team_totals" in market and ("corner" in market or "card" in market or "shot" in market)


def _btts_probability(prediction: Prediction) -> float:
    p_home_scores = 1.0 - math.exp(-prediction.expected_home_goals)
    p_away_scores = 1.0 - math.exp(-prediction.expected_away_goals)
    return p_home_scores * p_away_scores


def _total_goals_evaluation(
    prediction: Prediction,
    outcome: str,
    point: float,
    max_goals: int = 12,
) -> MarketEvaluation:
    total_lambda = prediction.expected_home_goals + prediction.expected_away_goals
    total_pmf = _poisson_pmf(total_lambda, max_goals)
    win = 0.0
    push = 0.0
    for goals, probability in enumerate(total_pmf):
        if outcome == "over" and goals > point:
            win += probability
        elif outcome == "under" and goals < point:
            win += probability
        elif _is_push(goals, point):
            push += probability
    return MarketEvaluation(win_prob=win, push_prob=push)


def _metric_total_evaluation(
    prediction: Prediction,
    metric: str,
    outcome: str,
    point: float,
) -> MarketEvaluation | None:
    expected_home, expected_away = _expected_metric_values(prediction, metric)
    if expected_home <= 0 and expected_away <= 0:
        return None
    return _total_count_evaluation(expected_home + expected_away, outcome, point, _max_count(metric))


def _spread_evaluation(
    prediction: Prediction,
    outcome: str,
    point: float,
    max_goals: int = 12,
) -> MarketEvaluation:
    home_pmf = _poisson_pmf(prediction.expected_home_goals, max_goals)
    away_pmf = _poisson_pmf(prediction.expected_away_goals, max_goals)
    win = 0.0
    push = 0.0
    for home_goals, home_prob in enumerate(home_pmf):
        for away_goals, away_prob in enumerate(away_pmf):
            probability = home_prob * away_prob
            if outcome == "home":
                adjusted_margin = home_goals + point - away_goals
            else:
                adjusted_margin = away_goals + point - home_goals
            if adjusted_margin > 0:
                win += probability
            elif abs(adjusted_margin) < 1e-9:
                push += probability
    return MarketEvaluation(win_prob=win, push_prob=push)


def _metric_spread_evaluation(
    prediction: Prediction,
    metric: str,
    outcome: str,
    point: float,
) -> MarketEvaluation | None:
    expected_home, expected_away = _expected_metric_values(prediction, metric)
    if expected_home <= 0 and expected_away <= 0:
        return None
    return _count_spread_evaluation(expected_home, expected_away, outcome, point, _max_count(metric))


def _team_total_evaluation(
    prediction: Prediction,
    quote: OddsQuote,
    max_goals: int = 12,
) -> MarketEvaluation | None:
    description = normalize_name(quote.description)
    if normalize_name(prediction.home_team) in description:
        expected_goals = prediction.expected_home_goals
    elif normalize_name(prediction.away_team) in description:
        expected_goals = prediction.expected_away_goals
    else:
        return None

    pmf = _poisson_pmf(expected_goals, max_goals)
    win = 0.0
    push = 0.0
    for goals, probability in enumerate(pmf):
        if quote.outcome == "over" and goals > quote.point:
            win += probability
        elif quote.outcome == "under" and goals < quote.point:
            win += probability
        elif quote.point is not None and _is_push(goals, quote.point):
            push += probability
    return MarketEvaluation(win_prob=win, push_prob=push)


def _metric_team_total_evaluation(
    prediction: Prediction,
    metric: str,
    quote: OddsQuote,
) -> MarketEvaluation | None:
    expected_home, expected_away = _expected_metric_values(prediction, metric)
    description = normalize_name(quote.description)
    if normalize_name(prediction.home_team) in description:
        expected_value = expected_home
    elif normalize_name(prediction.away_team) in description:
        expected_value = expected_away
    else:
        return None
    if expected_value <= 0:
        return None
    return _total_count_evaluation(expected_value, quote.outcome, quote.point, _max_count(metric))


def _total_count_evaluation(
    expected_value: float,
    outcome: str,
    point: float | None,
    max_count: int,
) -> MarketEvaluation:
    if point is None:
        return MarketEvaluation(0.0)
    pmf = _poisson_pmf(expected_value, max_count)
    win = 0.0
    push = 0.0
    for actual, probability in enumerate(pmf):
        if outcome == "over" and actual > point:
            win += probability
        elif outcome == "under" and actual < point:
            win += probability
        elif _is_push(actual, point):
            push += probability
    return MarketEvaluation(win_prob=win, push_prob=push)


def _count_spread_evaluation(
    expected_home: float,
    expected_away: float,
    outcome: str,
    point: float,
    max_count: int,
) -> MarketEvaluation:
    home_pmf = _poisson_pmf(expected_home, max_count)
    away_pmf = _poisson_pmf(expected_away, max_count)
    win = 0.0
    push = 0.0
    for home_value, home_prob in enumerate(home_pmf):
        for away_value, away_prob in enumerate(away_pmf):
            probability = home_prob * away_prob
            if outcome == "home":
                adjusted_margin = home_value + point - away_value
            else:
                adjusted_margin = away_value + point - home_value
            if adjusted_margin > 0:
                win += probability
            elif abs(adjusted_margin) < 1e-9:
                push += probability
    return MarketEvaluation(win_prob=win, push_prob=push)


def _max_count(metric: str) -> int:
    return {
        "corners": 24,
        "shots_on_target": 24,
        "shots": 60,
        "cards": 18,
    }.get(metric, 30)


def _poisson_pmf(expected_goals: float, max_goals: int) -> list[float]:
    probabilities = [
        math.exp(-expected_goals) * (expected_goals**goals) / math.factorial(goals)
        for goals in range(max_goals + 1)
    ]
    total = sum(probabilities)
    return [probability / total for probability in probabilities]


def _is_push(actual: int, point: float) -> bool:
    return abs(actual - point) < 1e-9


def _confidence(prediction: Prediction) -> str:
    if prediction.evidence_matches >= 16 and prediction.expected_home_goals > 0:
        return "media"
    if prediction.evidence_matches >= 8:
        return "baixa-media"
    return "baixa"


def _reason(edge: float, probability: float, decimal_odds: float, min_edge: float) -> str:
    if edge >= min_edge:
        return (
            f"EV positivo: probabilidade estimada {probability:.1%} contra odd "
            f"{decimal_odds:.2f}, edge {edge:.1%}."
        )
    return (
        f"Sem valor suficiente: edge {edge:.1%} abaixo do minimo "
        f"{min_edge:.1%}."
    )


def _sort_key(sort_by: str):
    if sort_by == "probability":
        return lambda item: (item.model_prob, item.edge, item.decimal_odds)
    if sort_by == "stake":
        return lambda item: (item.stake, item.edge, item.model_prob)
    return lambda item: (item.edge, item.model_prob, item.stake)
