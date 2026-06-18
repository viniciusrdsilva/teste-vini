from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from .models import Fixture, OddsQuote


_STOPWORDS = {
    "fc",
    "cf",
    "sc",
    "afc",
    "club",
    "the",
    "de",
    "da",
    "do",
    "ac",
}


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower()
    ascii_text = re.sub(r"[^a-z0-9]+", " ", ascii_text)
    tokens = [token for token in ascii_text.split() if token not in _STOPWORDS]
    return " ".join(tokens)


def normalize_bookmaker(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def normalize_outcome(value: str, home_team: str, away_team: str) -> str:
    raw = value.strip().lower()
    if raw in {"home", "h", "1"}:
        return "home"
    if raw in {"draw", "d", "x", "tie"}:
        return "draw"
    if raw in {"away", "a", "2"}:
        return "away"

    normalized = normalize_name(value)
    if normalized == normalize_name(home_team):
        return "home"
    if normalized == normalize_name(away_team):
        return "away"
    if normalized in {"draw", "empate"}:
        return "draw"
    raise ValueError(
        f"Outcome '{value}' nao corresponde a home/draw/away nem aos times."
    )


def normalize_market_outcome(
    market: str,
    value: str,
    home_team: str,
    away_team: str,
) -> str:
    market_key = market.strip().lower()
    raw = value.strip().lower()
    normalized = normalize_name(value)

    if (
        market_key in {"h2h", "h2h_3_way", "spreads", "alternate_spreads", "draw_no_bet"}
        or "spreads" in market_key
    ):
        return normalize_outcome(value, home_team, away_team)

    if (
        market_key in {
        "totals",
        "alternate_totals",
        "team_totals",
        "alternate_team_totals",
        "totals_h1",
        "totals_h2",
        }
        or "totals" in market_key
        or market_key in {"player_shots", "player_shots_on_target"}
    ):
        if raw in {"over", "o", "mais"} or normalized in {"over", "mais"}:
            return "over"
        if raw in {"under", "u", "menos"} or normalized in {"under", "menos"}:
            return "under"

    if market_key == "btts" or market_key.startswith("player_goal_scorer") or market_key.startswith("player_to_receive"):
        if raw in {"yes", "sim", "y"} or normalized in {"yes", "sim"}:
            return "yes"
        if raw in {"no", "nao", "n"} or normalized in {"no", "nao"}:
            return "no"

    if market_key == "double_chance":
        if _contains_team(value, home_team) and _contains_draw(value):
            return "home_draw"
        if _contains_team(value, away_team) and _contains_draw(value):
            return "draw_away"
        if _contains_team(value, home_team) and _contains_team(value, away_team):
            return "home_away"
        if raw in {"1x", "home_draw"}:
            return "home_draw"
        if raw in {"x2", "draw_away"}:
            return "draw_away"
        if raw in {"12", "home_away"}:
            return "home_away"

    return raw.replace(" ", "_")


def _contains_team(value: str, team: str) -> bool:
    return normalize_name(team) in normalize_name(value)


def _contains_draw(value: str) -> bool:
    normalized = normalize_name(value)
    return "draw" in normalized.split() or "empate" in normalized.split()


def fixture_signature(home_team: str, away_team: str) -> tuple[str, str]:
    return normalize_name(home_team), normalize_name(away_team)


def find_fixture_for_quote(
    quote: OddsQuote,
    fixtures: list[Fixture],
    kickoff_tolerance_hours: float = 36.0,
) -> Fixture | None:
    if quote.fixture_id:
        for fixture in fixtures:
            if fixture.fixture_id == quote.fixture_id:
                return fixture

    quote_sig = fixture_signature(quote.home_team, quote.away_team)
    candidates = [
        fixture
        for fixture in fixtures
        if fixture_signature(fixture.home_team, fixture.away_team) == quote_sig
    ]
    if not candidates:
        return None
    if quote.kickoff_utc is None:
        return candidates[0]

    def distance_hours(candidate_time: datetime | None) -> float:
        if candidate_time is None:
            return float("inf")
        return abs((candidate_time - quote.kickoff_utc).total_seconds()) / 3600.0

    best = min(candidates, key=lambda fixture: distance_hours(fixture.kickoff_utc))
    if distance_hours(best.kickoff_utc) <= kickoff_tolerance_hours:
        return best
    return None
