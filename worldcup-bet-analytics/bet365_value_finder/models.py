from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


OUTCOMES = ("home", "draw", "away")


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    kickoff_utc: datetime | None
    home_team: str
    away_team: str
    competition: str = ""


@dataclass(frozen=True)
class MatchResult:
    kickoff_utc: datetime | None
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    competition: str = ""


@dataclass(frozen=True)
class StatResult:
    kickoff_utc: datetime | None
    home_team: str
    away_team: str
    metric: str
    home_value: float
    away_value: float
    competition: str = ""


@dataclass(frozen=True)
class OddsQuote:
    event_id: str
    fixture_id: str
    kickoff_utc: datetime | None
    home_team: str
    away_team: str
    bookmaker_key: str
    bookmaker_title: str
    market: str
    outcome: str
    decimal_odds: float
    point: float | None = None
    description: str = ""
    last_update: datetime | None = None


@dataclass(frozen=True)
class Prediction:
    fixture_id: str
    home_team: str
    away_team: str
    p_home: float
    p_draw: float
    p_away: float
    expected_home_goals: float
    expected_away_goals: float
    model_name: str
    evidence_matches: int
    expected_metrics: dict[str, tuple[float, float]] = field(default_factory=dict)

    def probability_for(self, outcome: str) -> float:
        if outcome == "home":
            return self.p_home
        if outcome == "draw":
            return self.p_draw
        if outcome == "away":
            return self.p_away
        raise ValueError(f"Outcome invalido: {outcome}")


@dataclass(frozen=True)
class Recommendation:
    title: str
    fixture_id: str
    kickoff_utc: datetime | None
    home_team: str
    away_team: str
    bookmaker: str
    market: str
    outcome: str
    point: float | None
    decimal_odds: float
    model_prob: float
    push_prob: float
    implied_prob: float
    fair_odds: float
    min_odds_for_edge: float
    edge: float
    kelly_fraction: float
    stake: float
    expected_home_goals: float
    expected_away_goals: float
    expected_metric: str
    expected_metric_home: float
    expected_metric_away: float
    confidence: str
    reason: str
