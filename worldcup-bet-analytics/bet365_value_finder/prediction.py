from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from .matching import normalize_bookmaker, normalize_name
from .models import Fixture, MatchResult, OddsQuote, Prediction, StatResult


@dataclass
class _TeamStats:
    home_for: int = 0
    home_against: int = 0
    home_games: int = 0
    away_for: int = 0
    away_against: int = 0
    away_games: int = 0

    @property
    def games(self) -> int:
        return self.home_games + self.away_games

    @property
    def total_for(self) -> int:
        return self.home_for + self.away_for

    @property
    def total_against(self) -> int:
        return self.home_against + self.away_against


class PoissonGoalModel:
    def __init__(self, min_games_for_full_strength: int = 8, max_goals: int = 10) -> None:
        self.min_games_for_full_strength = min_games_for_full_strength
        self.max_goals = max_goals
        self._teams: dict[str, _TeamStats] = defaultdict(_TeamStats)
        self._team_names: dict[str, str] = {}
        self._league_home_avg = 1.35
        self._league_away_avg = 1.05
        self._total_matches = 0

    @property
    def total_matches(self) -> int:
        return self._total_matches

    def fit(self, results: list[MatchResult]) -> "PoissonGoalModel":
        self._teams = defaultdict(_TeamStats)
        self._team_names = {}
        self._total_matches = len(results)
        if not results:
            return self

        home_goals = 0
        away_goals = 0
        for result in results:
            home_key = normalize_name(result.home_team)
            away_key = normalize_name(result.away_team)
            self._team_names[home_key] = result.home_team
            self._team_names[away_key] = result.away_team

            home = self._teams[home_key]
            away = self._teams[away_key]
            home.home_for += result.home_goals
            home.home_against += result.away_goals
            home.home_games += 1
            away.away_for += result.away_goals
            away.away_against += result.home_goals
            away.away_games += 1
            home_goals += result.home_goals
            away_goals += result.away_goals

        self._league_home_avg = max(0.2, home_goals / len(results))
        self._league_away_avg = max(0.2, away_goals / len(results))
        return self

    def predict(self, fixture: Fixture) -> Prediction:
        home_key = normalize_name(fixture.home_team)
        away_key = normalize_name(fixture.away_team)
        home_stats = self._teams.get(home_key, _TeamStats())
        away_stats = self._teams.get(away_key, _TeamStats())

        home_attack = self._home_attack(home_stats)
        away_defence = self._away_defence(away_stats)
        away_attack = self._away_attack(away_stats)
        home_defence = self._home_defence(home_stats)

        expected_home = self._clip_goal_rate(
            self._league_home_avg * home_attack * away_defence
        )
        expected_away = self._clip_goal_rate(
            self._league_away_avg * away_attack * home_defence
        )
        p_home, p_draw, p_away = self._match_probabilities(expected_home, expected_away)

        evidence = home_stats.games + away_stats.games
        return Prediction(
            fixture_id=fixture.fixture_id,
            home_team=fixture.home_team,
            away_team=fixture.away_team,
            p_home=p_home,
            p_draw=p_draw,
            p_away=p_away,
            expected_home_goals=expected_home,
            expected_away_goals=expected_away,
            model_name="poisson_goals",
            evidence_matches=evidence,
        )

    def _overall_attack(self, stats: _TeamStats) -> float:
        if stats.games == 0:
            return 1.0
        league_avg = (self._league_home_avg + self._league_away_avg) / 2
        raw = (stats.total_for / stats.games) / league_avg
        return self._shrink(raw, stats.games)

    def _overall_defence(self, stats: _TeamStats) -> float:
        if stats.games == 0:
            return 1.0
        league_avg = (self._league_home_avg + self._league_away_avg) / 2
        raw = (stats.total_against / stats.games) / league_avg
        return self._shrink(raw, stats.games)

    def _home_attack(self, stats: _TeamStats) -> float:
        if stats.home_games == 0:
            return self._overall_attack(stats)
        raw = (stats.home_for / stats.home_games) / self._league_home_avg
        return self._shrink(raw, stats.home_games)

    def _away_attack(self, stats: _TeamStats) -> float:
        if stats.away_games == 0:
            return self._overall_attack(stats)
        raw = (stats.away_for / stats.away_games) / self._league_away_avg
        return self._shrink(raw, stats.away_games)

    def _home_defence(self, stats: _TeamStats) -> float:
        if stats.home_games == 0:
            return self._overall_defence(stats)
        raw = (stats.home_against / stats.home_games) / self._league_away_avg
        return self._shrink(raw, stats.home_games)

    def _away_defence(self, stats: _TeamStats) -> float:
        if stats.away_games == 0:
            return self._overall_defence(stats)
        raw = (stats.away_against / stats.away_games) / self._league_home_avg
        return self._shrink(raw, stats.away_games)

    def _shrink(self, raw_ratio: float, games: int) -> float:
        bounded = min(2.5, max(0.25, raw_ratio))
        weight = min(1.0, games / self.min_games_for_full_strength)
        return 1.0 + (bounded - 1.0) * weight

    @staticmethod
    def _clip_goal_rate(rate: float) -> float:
        return min(4.5, max(0.15, rate))

    def _match_probabilities(self, expected_home: float, expected_away: float) -> tuple[float, float, float]:
        home_pmf = _poisson_pmf(expected_home, self.max_goals)
        away_pmf = _poisson_pmf(expected_away, self.max_goals)
        p_home = 0.0
        p_draw = 0.0
        p_away = 0.0
        for home_goals, home_prob in enumerate(home_pmf):
            for away_goals, away_prob in enumerate(away_pmf):
                probability = home_prob * away_prob
                if home_goals > away_goals:
                    p_home += probability
                elif home_goals == away_goals:
                    p_draw += probability
                else:
                    p_away += probability
        total = p_home + p_draw + p_away
        return p_home / total, p_draw / total, p_away / total


class MetricPoissonModel:
    def __init__(self, metric: str, min_games_for_full_strength: int = 8) -> None:
        self.metric = metric
        self.min_games_for_full_strength = min_games_for_full_strength
        self._teams: dict[str, _TeamStats] = defaultdict(_TeamStats)
        self._league_home_avg = _default_metric_average(metric)[0]
        self._league_away_avg = _default_metric_average(metric)[1]
        self._total_matches = 0

    @property
    def total_matches(self) -> int:
        return self._total_matches

    def fit(self, stats: list[StatResult]) -> "MetricPoissonModel":
        rows = [row for row in stats if row.metric == self.metric]
        self._teams = defaultdict(_TeamStats)
        self._total_matches = len(rows)
        if not rows:
            return self

        home_total = 0.0
        away_total = 0.0
        for row in rows:
            home_key = normalize_name(row.home_team)
            away_key = normalize_name(row.away_team)
            home = self._teams[home_key]
            away = self._teams[away_key]
            home.home_for += row.home_value
            home.home_against += row.away_value
            home.home_games += 1
            away.away_for += row.away_value
            away.away_against += row.home_value
            away.away_games += 1
            home_total += row.home_value
            away_total += row.away_value

        self._league_home_avg = max(0.05, home_total / len(rows))
        self._league_away_avg = max(0.05, away_total / len(rows))
        return self

    def predict(self, fixture: Fixture) -> tuple[float, float, int]:
        home_key = normalize_name(fixture.home_team)
        away_key = normalize_name(fixture.away_team)
        home_stats = self._teams.get(home_key, _TeamStats())
        away_stats = self._teams.get(away_key, _TeamStats())

        home_attack = self._home_attack(home_stats)
        away_defence = self._away_defence(away_stats)
        away_attack = self._away_attack(away_stats)
        home_defence = self._home_defence(home_stats)

        expected_home = self._clip_rate(self._league_home_avg * home_attack * away_defence)
        expected_away = self._clip_rate(self._league_away_avg * away_attack * home_defence)
        return expected_home, expected_away, home_stats.games + away_stats.games

    def _overall_attack(self, stats: _TeamStats) -> float:
        if stats.games == 0:
            return 1.0
        league_avg = (self._league_home_avg + self._league_away_avg) / 2
        raw = (stats.total_for / stats.games) / league_avg
        return self._shrink(raw, stats.games)

    def _overall_defence(self, stats: _TeamStats) -> float:
        if stats.games == 0:
            return 1.0
        league_avg = (self._league_home_avg + self._league_away_avg) / 2
        raw = (stats.total_against / stats.games) / league_avg
        return self._shrink(raw, stats.games)

    def _home_attack(self, stats: _TeamStats) -> float:
        if stats.home_games == 0:
            return self._overall_attack(stats)
        raw = (stats.home_for / stats.home_games) / self._league_home_avg
        return self._shrink(raw, stats.home_games)

    def _away_attack(self, stats: _TeamStats) -> float:
        if stats.away_games == 0:
            return self._overall_attack(stats)
        raw = (stats.away_for / stats.away_games) / self._league_away_avg
        return self._shrink(raw, stats.away_games)

    def _home_defence(self, stats: _TeamStats) -> float:
        if stats.home_games == 0:
            return self._overall_defence(stats)
        raw = (stats.home_against / stats.home_games) / self._league_away_avg
        return self._shrink(raw, stats.home_games)

    def _away_defence(self, stats: _TeamStats) -> float:
        if stats.away_games == 0:
            return self._overall_defence(stats)
        raw = (stats.away_against / stats.away_games) / self._league_home_avg
        return self._shrink(raw, stats.away_games)

    def _shrink(self, raw_ratio: float, games: int) -> float:
        bounded = min(2.5, max(0.25, raw_ratio))
        weight = min(1.0, games / self.min_games_for_full_strength)
        return 1.0 + (bounded - 1.0) * weight

    def _clip_rate(self, rate: float) -> float:
        upper_bound = {
            "corners": 12.0,
            "shots_on_target": 12.0,
            "shots": 30.0,
            "cards": 8.0,
        }.get(self.metric, 20.0)
        return min(upper_bound, max(0.01, rate))


def enrich_predictions_with_stats(
    predictions: dict[str, Prediction],
    fixtures: list[Fixture],
    stats: list[StatResult],
) -> dict[str, Prediction]:
    if not stats:
        return predictions

    metrics = sorted({row.metric for row in stats})
    metric_predictions: dict[str, dict[str, tuple[float, float]]] = {
        fixture.fixture_id: {} for fixture in fixtures
    }
    metric_evidence: dict[str, int] = {fixture.fixture_id: 0 for fixture in fixtures}
    for metric in metrics:
        model = MetricPoissonModel(metric).fit(stats)
        for fixture in fixtures:
            expected_home, expected_away, evidence = model.predict(fixture)
            metric_predictions[fixture.fixture_id][metric] = (expected_home, expected_away)
            metric_evidence[fixture.fixture_id] = max(metric_evidence[fixture.fixture_id], evidence)

    enriched: dict[str, Prediction] = {}
    for fixture_id, prediction in predictions.items():
        expected_metrics = dict(prediction.expected_metrics)
        expected_metrics.update(metric_predictions.get(fixture_id, {}))
        enriched[fixture_id] = Prediction(
            fixture_id=prediction.fixture_id,
            home_team=prediction.home_team,
            away_team=prediction.away_team,
            p_home=prediction.p_home,
            p_draw=prediction.p_draw,
            p_away=prediction.p_away,
            expected_home_goals=prediction.expected_home_goals,
            expected_away_goals=prediction.expected_away_goals,
            model_name=prediction.model_name,
            evidence_matches=max(
                prediction.evidence_matches,
                metric_evidence.get(fixture_id, 0),
            ),
            expected_metrics=expected_metrics,
        )
    return enriched


def _default_metric_average(metric: str) -> tuple[float, float]:
    return {
        "corners": (5.0, 4.5),
        "shots_on_target": (4.5, 3.8),
        "shots": (13.0, 11.0),
        "cards": (2.0, 2.2),
    }.get(metric, (1.0, 1.0))


def _poisson_pmf(expected_goals: float, max_goals: int) -> list[float]:
    return [
        math.exp(-expected_goals) * (expected_goals**goals) / math.factorial(goals)
        for goals in range(max_goals + 1)
    ]


def consensus_predictions_from_odds(
    fixtures: list[Fixture],
    quotes_by_fixture: dict[str, list[OddsQuote]],
    excluded_bookmaker: str,
) -> dict[str, Prediction]:
    excluded_key = normalize_bookmaker(excluded_bookmaker)
    predictions: dict[str, Prediction] = {}
    for fixture in fixtures:
        quotes = [
            quote
            for quote in quotes_by_fixture.get(fixture.fixture_id, [])
            if quote.bookmaker_key != excluded_key and quote.market == "h2h"
        ]
        if not quotes:
            continue

        grouped: dict[str, list[float]] = {"home": [], "draw": [], "away": []}
        for quote in quotes:
            if quote.decimal_odds > 1.0 and quote.outcome in grouped:
                grouped[quote.outcome].append(1.0 / quote.decimal_odds)
        if not all(grouped.values()):
            continue

        raw = {outcome: sum(values) / len(values) for outcome, values in grouped.items()}
        overround = sum(raw.values())
        if overround <= 0:
            continue
        p_home = raw["home"] / overround
        p_draw = raw["draw"] / overround
        p_away = raw["away"] / overround
        predictions[fixture.fixture_id] = Prediction(
            fixture_id=fixture.fixture_id,
            home_team=fixture.home_team,
            away_team=fixture.away_team,
            p_home=p_home,
            p_draw=p_draw,
            p_away=p_away,
            expected_home_goals=0.0,
            expected_away_goals=0.0,
            model_name="market_consensus",
            evidence_matches=len(quotes),
        )
    return predictions


def blend_predictions(
    model_predictions: dict[str, Prediction],
    consensus_predictions: dict[str, Prediction],
    model_weight: float,
) -> dict[str, Prediction]:
    weight = min(1.0, max(0.0, model_weight))
    blended: dict[str, Prediction] = {}
    for fixture_id, model in model_predictions.items():
        consensus = consensus_predictions.get(fixture_id)
        if consensus is None:
            blended[fixture_id] = model
            continue
        blended[fixture_id] = Prediction(
            fixture_id=fixture_id,
            home_team=model.home_team,
            away_team=model.away_team,
            p_home=(model.p_home * weight) + (consensus.p_home * (1.0 - weight)),
            p_draw=(model.p_draw * weight) + (consensus.p_draw * (1.0 - weight)),
            p_away=(model.p_away * weight) + (consensus.p_away * (1.0 - weight)),
            expected_home_goals=model.expected_home_goals,
            expected_away_goals=model.expected_away_goals,
            model_name=f"blend({model.model_name},{consensus.model_name})",
            evidence_matches=model.evidence_matches,
            expected_metrics=dict(model.expected_metrics),
        )
    return blended
