from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from .models import Recommendation
from .timeutils import iso_z


def recommendation_to_dict(item: Recommendation) -> dict[str, object]:
    data = asdict(item)
    data["kickoff_utc"] = iso_z(item.kickoff_utc)
    return data


def write_recommendations_csv(path: str | Path, recommendations: list[Recommendation]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [recommendation_to_dict(item) for item in recommendations]
    fieldnames = [
        "title",
        "fixture_id",
        "kickoff_utc",
        "home_team",
        "away_team",
        "bookmaker",
        "market",
        "outcome",
        "point",
        "decimal_odds",
        "model_prob",
        "push_prob",
        "implied_prob",
        "fair_odds",
        "min_odds_for_edge",
        "edge",
        "kelly_fraction",
        "stake",
        "expected_home_goals",
        "expected_away_goals",
        "expected_metric",
        "expected_metric_home",
        "expected_metric_away",
        "confidence",
        "reason",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_recommendations_json(path: str | Path, recommendations: list[Recommendation]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [recommendation_to_dict(item) for item in recommendations]
    output_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def render_table(
    recommendations: list[Recommendation],
    limit: int = 20,
    title: str = "Melhores apostas encontradas",
) -> str:
    if not recommendations:
        return f"{title}\n\nNenhuma aposta passou pelos filtros configurados."

    headers = [
        "Jogo",
        "Titulo",
        "Mercado",
        "Odd",
        "Prob",
        "Edge",
        "Stake",
        "Esp.",
        "Conf.",
    ]
    rows = []
    for item in recommendations[:limit]:
        rows.append(
            [
                f"{item.home_team} x {item.away_team}",
                item.title,
                _market_label(item),
                f"{item.decimal_odds:.2f}",
                f"{item.model_prob:.1%}",
                f"{item.edge:.1%}",
                f"{item.stake:.2f}",
                _expected_label(item),
                item.confidence,
            ]
        )
    widths = [
        max(len(str(row[index])) for row in [headers, *rows])
        for index in range(len(headers))
    ]

    def format_row(row: list[str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))

    divider = "-+-".join("-" * width for width in widths)
    lines = [title, "", format_row(headers), divider]
    lines.extend(format_row(row) for row in rows)
    return "\n".join(lines)


def _market_label(item: Recommendation) -> str:
    if item.point is None:
        return item.market
    return f"{item.market} {item.point:g}"


def _expected_label(item: Recommendation) -> str:
    if item.expected_metric == "goals":
        metric = "gols"
    elif item.expected_metric == "corners":
        metric = "esc."
    elif item.expected_metric == "shots_on_target":
        metric = "SOT"
    elif item.expected_metric == "shots":
        metric = "chutes"
    elif item.expected_metric == "cards":
        metric = "cart."
    else:
        metric = item.expected_metric
    return f"{item.expected_metric_home:.1f}-{item.expected_metric_away:.1f} {metric}"
