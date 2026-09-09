"""Fork: the median-unit-cost reference behind the pricing settings."""

import pytest

from backend.app.services.print_price import median, unit_cost

# ---------------------------------------------------------------------- median


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], None),
        ([4.0], 4.0),
        ([3.0, 1.0, 2.0], 2.0),
        ([1.0, 2.0, 3.0, 4.0], 2.5),
    ],
)
def test_median(values, expected):
    assert median(values) == expected


def test_median_leaves_the_input_alone():
    """Sorting in place would reorder the caller's list as a side effect."""
    values = [3.0, 1.0, 2.0]
    median(values)
    assert values == [3.0, 1.0, 2.0]


def test_median_resists_one_wild_print():
    """The point of a median here: one exotic filament must not set the scale."""
    typical = [2.0, 2.1, 2.2, 2.3, 2.4]
    assert median([*typical, 400.0]) < 3.0


# ------------------------------------------------------------------- unit cost


def test_unit_cost_adds_filament_and_energy():
    assert unit_cost(2.5, 0.4) == pytest.approx(2.9)


def test_unit_cost_treats_a_missing_energy_reading_as_zero():
    """A plug with no energy meter is a normal setup, not a reason to drop
    the print from the sample."""
    assert unit_cost(2.5, None) == pytest.approx(2.5)


def test_unit_cost_treats_a_missing_filament_cost_as_zero():
    assert unit_cost(None, 0.4) == pytest.approx(0.4)


def test_unit_cost_is_none_when_nothing_is_known():
    """Distinct from 0.0, which would mean "this print was free"."""
    assert unit_cost(None, None) is None
