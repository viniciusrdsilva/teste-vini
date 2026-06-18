from datetime import date

from bet365_value_finder.clients import load_stats_csv
from bet365_value_finder.models import Fixture, OddsQuote, Prediction
from bet365_value_finder.prediction import enrich_predictions_with_stats
from bet365_value_finder.recommendation import (
    attach_quotes_to_fixtures,
    evaluate_market,
    recommend_value_bets,
)
from bet365_value_finder.timeutils import date_end_utc, iso_z_seconds


def test_recommendation_detects_positive_edge_and_caps_stake() -> None:
    fixture = Fixture("fixture-1", None, "Home", "Away")
    quote = OddsQuote(
        event_id="fixture-1",
        fixture_id="fixture-1",
        kickoff_utc=None,
        home_team="Home",
        away_team="Away",
        bookmaker_key="bet365",
        bookmaker_title="bet365",
        market="h2h",
        outcome="home",
        decimal_odds=2.10,
    )
    prediction = Prediction(
        fixture_id="fixture-1",
        home_team="Home",
        away_team="Away",
        p_home=0.55,
        p_draw=0.25,
        p_away=0.20,
        expected_home_goals=1.6,
        expected_away_goals=0.9,
        model_name="test",
        evidence_matches=20,
    )

    recommendations = recommend_value_bets(
        fixtures=[fixture],
        predictions={"fixture-1": prediction},
        quotes_by_fixture=attach_quotes_to_fixtures([fixture], [quote]),
        bookmaker="bet365",
        bankroll=1000,
        min_edge=0.03,
        kelly_fraction=0.25,
        max_stake_pct=0.02,
    )

    assert len(recommendations) == 1
    assert recommendations[0].edge > 0.03
    assert recommendations[0].stake <= 20.0


def test_api_datetime_format_has_no_microseconds() -> None:
    assert iso_z_seconds(date_end_utc(date(2026, 6, 19))) == "2026-06-19T23:59:59Z"


def test_total_goals_market_uses_point() -> None:
    prediction = Prediction(
        fixture_id="fixture-1",
        home_team="Home",
        away_team="Away",
        p_home=0.45,
        p_draw=0.30,
        p_away=0.25,
        expected_home_goals=1.6,
        expected_away_goals=1.2,
        model_name="test",
        evidence_matches=20,
    )
    quote = OddsQuote(
        event_id="fixture-1",
        fixture_id="fixture-1",
        kickoff_utc=None,
        home_team="Home",
        away_team="Away",
        bookmaker_key="bet365",
        bookmaker_title="bet365",
        market="totals",
        outcome="over",
        decimal_odds=1.90,
        point=2.5,
    )

    evaluation = evaluate_market(prediction, quote)

    assert evaluation is not None
    assert evaluation.win_prob > 0.0
    assert evaluation.push_prob == 0.0


def test_corner_total_market_uses_stats_model(tmp_path) -> None:
    stats_csv = tmp_path / "stats.csv"
    stats_csv.write_text(
        "\n".join(
            [
                "kickoff_utc,home_team,away_team,home_corners,away_corners,home_shots_on_target,away_shots_on_target,home_shots,away_shots,home_cards,away_cards,competition",
                "2026-01-01T12:00:00Z,Home,Away,8,4,5,3,14,9,2,3,TEST",
                "2026-01-02T12:00:00Z,Away,Home,3,9,2,6,8,15,3,1,TEST",
            ]
        ),
        encoding="utf-8",
    )
    fixture = Fixture("fixture-1", None, "Home", "Away")
    prediction = Prediction(
        fixture_id="fixture-1",
        home_team="Home",
        away_team="Away",
        p_home=0.45,
        p_draw=0.30,
        p_away=0.25,
        expected_home_goals=1.4,
        expected_away_goals=1.0,
        model_name="test",
        evidence_matches=2,
    )
    enriched = enrich_predictions_with_stats(
        {"fixture-1": prediction},
        [fixture],
        load_stats_csv(stats_csv),
    )
    quote = OddsQuote(
        event_id="fixture-1",
        fixture_id="fixture-1",
        kickoff_utc=None,
        home_team="Home",
        away_team="Away",
        bookmaker_key="bet365",
        bookmaker_title="bet365",
        market="alternate_totals_corners",
        outcome="over",
        decimal_odds=1.90,
        point=8.5,
    )

    evaluation = evaluate_market(enriched["fixture-1"], quote)

    assert "corners" in enriched["fixture-1"].expected_metrics
    assert evaluation is not None
    assert evaluation.win_prob > 0.0
