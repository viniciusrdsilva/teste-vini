from bet365_value_finder.models import Fixture, MatchResult
from bet365_value_finder.prediction import PoissonGoalModel


def test_poisson_prediction_probabilities_sum_to_one() -> None:
    results = [
        MatchResult(None, "Strong", "Weak", 3, 0),
        MatchResult(None, "Strong", "Average", 2, 0),
        MatchResult(None, "Weak", "Strong", 0, 2),
        MatchResult(None, "Average", "Weak", 1, 0),
    ]

    prediction = PoissonGoalModel().fit(results).predict(
        Fixture("fixture-1", None, "Strong", "Weak")
    )

    assert prediction.p_home > prediction.p_away
    assert round(prediction.p_home + prediction.p_draw + prediction.p_away, 8) == 1.0

