"""Tests for bnbagent.utils.amounts (human <-> raw token unit conversion)."""

from __future__ import annotations

from bnbagent.utils import from_raw, to_raw


class TestToRaw:
    def test_string_decimal(self):
        assert to_raw("1.5", 18) == 1_500_000_000_000_000_000

    def test_integer(self):
        assert to_raw(2, 18) == 2 * 10**18

    def test_float_precision_safe(self):
        # 1.1 is not exactly representable as float; Decimal(str(...)) keeps it exact.
        assert to_raw(1.1, 18) == 1_100_000_000_000_000_000

    def test_zero(self):
        assert to_raw("0", 18) == 0

    def test_small_fraction(self):
        assert to_raw("0.000000000000000001", 18) == 1

    def test_six_decimals(self):
        assert to_raw("1.5", 6) == 1_500_000


class TestFromRaw:
    def test_whole(self):
        assert from_raw(2 * 10**18, 18) == "2"

    def test_fraction(self):
        assert from_raw(1_500_000_000_000_000_000, 18) == "1.5"

    def test_one_wei(self):
        assert from_raw(1, 18) == "0.000000000000000001"

    def test_zero(self):
        assert from_raw(0, 18) == "0"

    def test_no_scientific_notation(self):
        # format(..., "f") must keep plain decimal output for tiny values.
        assert "e" not in from_raw(1, 18).lower()


class TestRoundTrip:
    def test_round_trip(self):
        for human in ("0.1", "1", "1.5", "123.456789", "0.000000000000000001"):
            assert from_raw(to_raw(human, 18), 18) == human
