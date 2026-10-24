from decimal import Decimal

import pytest

from backend.domain.money import format_inr, format_inr_lines, to_money


@pytest.mark.parametrize("value, expected", [
    ("1999", Decimal("1999.00")),
    (1999, Decimal("1999.00")),
    (Decimal("19.995"), Decimal("20.00")),  # half up to the paisa
    (Decimal("19.994"), Decimal("19.99")),
    ("-300", Decimal("-300.00")),
])
def test_to_money_is_exact(value, expected):
    assert to_money(value) == expected
    assert to_money(value).as_tuple().exponent == -2


@pytest.mark.parametrize("value", [19.99, True])
def test_floats_are_refused(value):
    with pytest.raises(TypeError):
        to_money(value)


@pytest.mark.parametrize("value", ["abc", "NaN", "Infinity", None])
def test_non_amounts_are_refused(value):
    with pytest.raises((ValueError, TypeError)):
        to_money(value)


@pytest.mark.parametrize("value, expected", [
    ("0", "₹0"),
    ("999", "₹999"),
    ("2655", "₹2,655"),
    ("124999", "₹1,24,999"),
    ("12345678", "₹1,23,45,678"),
    ("1999.50", "₹1,999.50"),
    ("-300", "− ₹300"),
])
def test_indian_grouping_and_paise_only_when_not_zero(value, expected):
    assert format_inr(value) == expected


def test_paise_can_be_forced():
    assert format_inr("2700", always_paise=True) == "₹2,700.00"


def test_one_line_with_paise_makes_every_line_show_paise():
    assert format_inr_lines(["2700", "300.50"]) == ["₹2,700.00", "₹300.50"]
    assert format_inr_lines(["2700", "300"]) == ["₹2,700", "₹300"]
