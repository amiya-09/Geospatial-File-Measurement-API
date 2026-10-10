import json
import math

import pytest
from shapely.geometry import Point, Polygon

from app.services.serialize import json_safe, to_geojson


def test_non_finite_floats_become_null_at_any_depth():
    value = {"a": float("nan"), "b": [1, float("inf"), {"c": float("-inf")}], "d": (2.5, math.nan)}
    cleaned = json_safe(value)
    assert cleaned == {"a": None, "b": [1, None, {"c": None}], "d": [2.5, None]}
    json.dumps(cleaned, allow_nan=False)  # strictly valid JSON


def test_ordinary_values_pass_through_unchanged():
    value = {"s": "text", "i": 3, "f": 2.5, "b": True, "n": None, "l": [1, "x"]}
    assert json_safe(value) == value


@pytest.mark.parametrize("odd", [b"bytes", {1, 2}, object])
def test_unserialisable_values_are_stringified_not_dropped_or_raised(odd):
    cleaned = json_safe({"k": odd})
    assert isinstance(cleaned["k"], str)
    json.dumps(cleaned, allow_nan=False)


def test_non_string_keys_are_stringified():
    assert json_safe({1: "a"}) == {"1": "a"}


def test_to_geojson_returns_plain_dicts_and_lists():
    gj = to_geojson(Polygon([(0, 0), (1, 0), (1, 1), (0, 0)]))
    assert gj["type"] == "Polygon"
    assert isinstance(gj["coordinates"], list) and isinstance(gj["coordinates"][0][0], list)
    assert to_geojson(Point(1, 2)) == {"type": "Point", "coordinates": [1.0, 2.0]}
