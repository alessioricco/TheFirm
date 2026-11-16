"""Individual checks on small, controlled inputs."""

from firm.verify.checks.lookahead import decision_points
from firm.verify.checks.sensitivity import perturbations


def test_decision_points_cover_to_the_last_bar() -> None:
    pts = decision_points(1000, 50)
    assert pts[-1] == 999 and pts[0] == 100 and len(pts) == 50
    assert decision_points(20, 500)[-1] == 19


def test_perturbations_skip_bools_and_strings_and_move_ints() -> None:
    cases = perturbations(
        {"fast": 10, "slow": 40, "flag": True, "mode": "x", "k": 0.5}, (-0.1, 0.1)
    )
    keys = {(k, s) for k, s, _ in cases}
    assert keys == {
        ("fast", -0.1),
        ("fast", 0.1),
        ("k", -0.1),
        ("k", 0.1),
        ("slow", -0.1),
        ("slow", 0.1),
    }
    fast_up = next(p for k, s, p in cases if (k, s) == ("fast", 0.1))
    assert fast_up["fast"] == 11
    tiny = perturbations({"n": 3}, (0.1,))
    assert tiny[0][2]["n"] == 4  # rounding back to 3 would not perturb: forced to move
