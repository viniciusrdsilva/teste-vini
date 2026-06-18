from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .matching import normalize_bookmaker, normalize_market_outcome
from .models import Fixture, MatchResult, OddsQuote, StatResult
from .timeutils import iso_z_seconds, parse_datetime


class ApiError(RuntimeError):
    pass


def _get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ApiError(f"HTTP {exc.code} ao chamar {_redact_url(url)}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Falha de rede ao chamar {_redact_url(url)}: {exc}") from exc
    return json.loads(payload)


def _redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted_query = [
        (key, "REDACTED" if key.lower() in {"apikey", "api_key", "token", "key"} else value)
        for key, value in query
    ]
    return urllib.parse.urlunsplit(
        parsed._replace(query=urllib.parse.urlencode(redacted_query))
    )


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


def _read_csv_text(payload: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(payload)))


def _load_url_text(url: str) -> str:
    request = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ApiError(f"HTTP {exc.code} ao chamar {_redact_url(url)}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Falha de rede ao chamar {_redact_url(url)}: {exc}") from exc


def load_fixtures_csv(path: str | Path) -> list[Fixture]:
    fixtures: list[Fixture] = []
    for row in _read_csv(path):
        fixtures.append(
            Fixture(
                fixture_id=row.get("fixture_id", "").strip(),
                kickoff_utc=parse_datetime(row.get("kickoff_utc")),
                home_team=row["home_team"].strip(),
                away_team=row["away_team"].strip(),
                competition=row.get("competition", "").strip(),
            )
        )
    return fixtures


def load_results_csv(path: str | Path) -> list[MatchResult]:
    results: list[MatchResult] = []
    for row in _read_csv(path):
        results.append(
            MatchResult(
                kickoff_utc=parse_datetime(row.get("kickoff_utc")),
                home_team=row["home_team"].strip(),
                away_team=row["away_team"].strip(),
                home_goals=int(row["home_goals"]),
                away_goals=int(row["away_goals"]),
                competition=row.get("competition", "").strip(),
            )
        )
    return results


def load_stats_csv(path: str | Path) -> list[StatResult]:
    return _load_stats_rows(_read_csv(path))


def load_stats_csv_url(url: str) -> list[StatResult]:
    return _load_stats_rows(_read_csv_text(_load_url_text(url)))


def _load_stats_rows(rows: list[dict[str, str]]) -> list[StatResult]:
    stats: list[StatResult] = []
    for row in rows:
        kickoff_utc = parse_datetime(row.get("kickoff_utc"))
        home_team = row["home_team"].strip()
        away_team = row["away_team"].strip()
        competition = row.get("competition", "").strip()
        for metric, home_column, away_column in _STAT_COLUMNS:
            home_value = row.get(home_column, "")
            away_value = row.get(away_column, "")
            if home_value == "" or away_value == "":
                continue
            stats.append(
                StatResult(
                    kickoff_utc=kickoff_utc,
                    home_team=home_team,
                    away_team=away_team,
                    metric=metric,
                    home_value=float(home_value),
                    away_value=float(away_value),
                    competition=competition,
                )
            )
    return stats


_STAT_COLUMNS = [
    ("corners", "home_corners", "away_corners"),
    ("shots_on_target", "home_shots_on_target", "away_shots_on_target"),
    ("shots", "home_shots", "away_shots"),
    ("cards", "home_cards", "away_cards"),
]


def load_footystats_matches_csv(path: str | Path) -> list[StatResult]:
    rows = _read_csv(path)
    competition = _footystats_competition_name(path)
    stats: list[StatResult] = []
    for row in rows:
        status = (row.get("status") or "").strip().lower()
        if status and status not in {"complete", "completed"}:
            continue
        home_team = (row.get("home_team_name") or "").strip()
        away_team = (row.get("away_team_name") or "").strip()
        if not home_team or not away_team:
            continue
        kickoff_utc = _parse_footystats_datetime(row.get("date_GMT"))
        for metric, home_column, away_column in _FOOTYSTATS_STAT_COLUMNS:
            home_value = row.get(home_column, "")
            away_value = row.get(away_column, "")
            if home_value == "" or away_value == "":
                continue
            stats.append(
                StatResult(
                    kickoff_utc=kickoff_utc,
                    home_team=home_team,
                    away_team=away_team,
                    metric=metric,
                    home_value=float(home_value),
                    away_value=float(away_value),
                    competition=competition,
                )
            )
    return stats


def load_footystats_dir(path: str | Path) -> list[StatResult]:
    stats: list[StatResult] = []
    for csv_path in sorted(Path(path).glob("*matches*stats.csv")):
        stats.extend(load_footystats_matches_csv(csv_path))
    return stats


_FOOTYSTATS_STAT_COLUMNS = [
    ("corners", "home_team_corner_count", "away_team_corner_count"),
    ("shots_on_target", "home_team_shots_on_target", "away_team_shots_on_target"),
    ("shots", "home_team_shots", "away_team_shots"),
    ("cards", "home_team_yellow_cards", "away_team_yellow_cards"),
]


def _parse_footystats_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    for fmt in ("%b %d %Y - %I:%M%p", "%b %d %Y - %H:%M"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _footystats_competition_name(path: str | Path) -> str:
    name = Path(path).name
    if "-matches-" in name:
        return name.split("-matches-", 1)[0].replace("-", " ")
    return Path(path).stem.replace("-", " ")


def load_odds_csv(path: str | Path) -> list[OddsQuote]:
    quotes: list[OddsQuote] = []
    for index, row in enumerate(_read_csv(path), start=1):
        home_team = row["home_team"].strip()
        away_team = row["away_team"].strip()
        market = row.get("market", "h2h").strip() or "h2h"
        outcome = normalize_market_outcome(market, row["outcome"], home_team, away_team)
        bookmaker = row["bookmaker"].strip()
        event_id = row.get("event_id", "").strip()
        fixture_id = row.get("fixture_id", "").strip()
        quotes.append(
            OddsQuote(
                event_id=event_id or fixture_id or f"csv-{index}",
                fixture_id=fixture_id,
                kickoff_utc=parse_datetime(row.get("kickoff_utc")),
                home_team=home_team,
                away_team=away_team,
                bookmaker_key=normalize_bookmaker(bookmaker),
                bookmaker_title=bookmaker,
                market=market,
                outcome=outcome,
                decimal_odds=float(row["decimal_odds"]),
                point=_optional_float(row.get("point")),
                description=(row.get("description") or "").strip(),
                last_update=parse_datetime(row.get("last_update")),
            )
        )
    return quotes


class FootballDataClient:
    base_url = "https://api.football-data.org/v4"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def competition_matches(
        self,
        competition: str,
        date_from: date,
        date_to: date,
        status: str | None = None,
    ) -> tuple[list[Fixture], list[MatchResult]]:
        params = {
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
        }
        if status:
            params["status"] = status
        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}/competitions/{competition}/matches?{query}"
        data = _get_json(url, {"X-Auth-Token": self.api_key})

        fixtures: list[Fixture] = []
        results: list[MatchResult] = []
        for item in data.get("matches", []):
            home_team = item.get("homeTeam", {}).get("name", "")
            away_team = item.get("awayTeam", {}).get("name", "")
            kickoff = parse_datetime(item.get("utcDate"))
            fixture_id = str(item.get("id", ""))
            competition_name = item.get("competition", {}).get("code") or competition
            status_value = item.get("status", "")
            score = item.get("score", {}).get("fullTime", {})
            home_goals = score.get("home")
            away_goals = score.get("away")

            if status_value == "FINISHED" and home_goals is not None and away_goals is not None:
                results.append(
                    MatchResult(
                        kickoff_utc=kickoff,
                        home_team=home_team,
                        away_team=away_team,
                        home_goals=int(home_goals),
                        away_goals=int(away_goals),
                        competition=competition_name,
                    )
                )
            else:
                fixtures.append(
                    Fixture(
                        fixture_id=fixture_id,
                        kickoff_utc=kickoff,
                        home_team=home_team,
                        away_team=away_team,
                        competition=competition_name,
                    )
                )
        return fixtures, results


class OddsApiClient:
    base_url = "https://api.the-odds-api.com/v4"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def odds(
        self,
        sport_key: str,
        regions: str = "eu",
        markets: str = "h2h",
        bookmaker: str | None = None,
        commence_from: datetime | None = None,
        commence_to: datetime | None = None,
    ) -> list[OddsQuote]:
        params = {
            "apiKey": self.api_key,
            "markets": markets,
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        }
        if bookmaker:
            params["bookmakers"] = normalize_bookmaker(bookmaker)
        else:
            params["regions"] = regions
        if commence_from:
            params["commenceTimeFrom"] = iso_z_seconds(commence_from)
        if commence_to:
            params["commenceTimeTo"] = iso_z_seconds(commence_to)

        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}/sports/{sport_key}/odds/?{query}"
        data = _get_json(url)
        return _parse_odds_api_response(data)

    def events(
        self,
        sport_key: str,
        commence_from: datetime | None = None,
        commence_to: datetime | None = None,
    ) -> list[Fixture]:
        params = {
            "apiKey": self.api_key,
            "dateFormat": "iso",
        }
        if commence_from:
            params["commenceTimeFrom"] = iso_z_seconds(commence_from)
        if commence_to:
            params["commenceTimeTo"] = iso_z_seconds(commence_to)
        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}/sports/{sport_key}/events?{query}"
        data = _get_json(url)
        return [
            Fixture(
                fixture_id=str(item.get("id", "")),
                kickoff_utc=parse_datetime(item.get("commence_time")),
                home_team=item.get("home_team", ""),
                away_team=item.get("away_team", ""),
                competition=item.get("sport_key", sport_key),
            )
            for item in data
        ]

    def event_odds(
        self,
        sport_key: str,
        event_id: str,
        regions: str = "eu",
        markets: str = "btts,double_chance",
        bookmaker: str | None = None,
    ) -> list[OddsQuote]:
        params = {
            "apiKey": self.api_key,
            "markets": markets,
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        }
        if bookmaker:
            params["bookmakers"] = normalize_bookmaker(bookmaker)
        else:
            params["regions"] = regions

        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}/sports/{sport_key}/events/{event_id}/odds?{query}"
        data = _get_json(url)
        return _parse_odds_api_response(data)


def _optional_float(value: str | float | int | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _parse_odds_api_response(data: dict[str, Any] | list[dict[str, Any]]) -> list[OddsQuote]:
    quotes: list[OddsQuote] = []
    events = [data] if isinstance(data, dict) else data
    for event in events:
        event_id = str(event.get("id", ""))
        home_team = event.get("home_team", "")
        away_team = event.get("away_team", "")
        kickoff = parse_datetime(event.get("commence_time"))
        for bookmaker in event.get("bookmakers", []):
            bookmaker_key = normalize_bookmaker(bookmaker.get("key", ""))
            bookmaker_title = bookmaker.get("title", bookmaker_key)
            last_update = parse_datetime(bookmaker.get("last_update"))
            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "h2h")
                market_update = parse_datetime(market.get("last_update")) or last_update
                for outcome_data in market.get("outcomes", []):
                    try:
                        outcome = normalize_market_outcome(
                            market_key,
                            outcome_data.get("name", ""),
                            home_team,
                            away_team,
                        )
                    except ValueError:
                        continue
                    quotes.append(
                        OddsQuote(
                            event_id=event_id,
                            fixture_id="",
                            kickoff_utc=kickoff,
                            home_team=home_team,
                            away_team=away_team,
                            bookmaker_key=bookmaker_key,
                            bookmaker_title=bookmaker_title,
                            market=market_key,
                            outcome=outcome,
                            decimal_odds=float(outcome_data["price"]),
                            point=_optional_float(outcome_data.get("point")),
                            description=outcome_data.get("description", "") or "",
                            last_update=market_update,
                        )
                    )
    return quotes
