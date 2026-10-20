"""Correctness tests for resolve-chc-region."""

import json

import pytest
from conftest import load_skill, run_skill
from weather_skills_core import UsageError

_TANA_BBOX = (0.48, 36.5972, -3.0402, 41.5605)
_KENYA_BBOX = (5.4141, 33.9098, -4.7053, 41.906)


@pytest.fixture(scope="module")
def mod():
    return load_skill("resolve-chc-region", "resolve")


@pytest.fixture
def resolve(mod):
    return mod.resolve_chc_region


def test_tana_basin_prints_bbox(capsys, resolve):
    run_skill(resolve, "Tana River basin")
    out = capsys.readouterr().out.strip()
    n, w, s, e = (float(v) for v in out.split("/"))
    assert (n, w, s, e) == pytest.approx(_TANA_BBOX, abs=1e-4)


def test_tana_basin_geojson_is_polygon(tmp_path, capsys, resolve):
    path = tmp_path / "tana.json"
    run_skill(resolve, "Tana River basin", "--geojson", str(path))
    captured = capsys.readouterr()
    assert "Wrote boundary polygon" in captured.err
    fc = json.loads(path.read_text())
    assert fc["type"] == "FeatureCollection"
    (feature,) = fc["features"]
    props = feature["properties"]
    assert props["name"] == "Tana River basin"
    assert props["region_name"] == "tana_river_basin"
    assert props["level"] == "basin"
    assert props["iso3"] == "KEN"
    assert props["country"] == "Kenya"
    assert "Tana Basin.shp" in props["source"]
    assert props["bbox"] == pytest.approx(list(_TANA_BBOX), abs=1e-4)
    # A real outline, not a four-corner rectangle.
    assert feature["geometry"]["type"] == "Polygon"
    assert len(feature["geometry"]["coordinates"][0]) > 5


@pytest.mark.parametrize("alias", ["Tana basin", "Tana River catchment", "tana catchment"])
def test_aliases(mod, alias):
    key, fc = mod.load(alias)
    assert key == "tana_river_basin"
    assert fc["features"][0]["properties"]["name"] == "Tana River basin"


def test_bare_tana_river_is_ambiguous(mod):
    """Bare "Tana River" is also Tana River County, so it must not pick the basin."""
    with pytest.raises(UsageError, match="kenya-tana_river"):
        mod.load("Tana River")


def test_unknown_region_points_to_resolve_region(resolve, capsys):
    with pytest.raises(SystemExit) as exc:
        run_skill(resolve, "Nile basin")
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Tana River basin" in err
    assert "resolve-region" in err


def test_kenya_is_knsdi_outline(tmp_path, capsys, resolve):
    path = tmp_path / "kenya.json"
    run_skill(resolve, "Kenya", "--geojson", str(path))
    out = capsys.readouterr().out.strip()
    assert tuple(float(v) for v in out.split("/")) == pytest.approx(_KENYA_BBOX, abs=1e-4)
    (feature,) = json.loads(path.read_text())["features"]
    assert feature["properties"]["level"] == "country"
    assert "KNSDI" in feature["properties"]["source"]
    assert feature["geometry"]["type"] == "MultiPolygon"
    # No sliver holes from gaps between the source counties.
    assert all(len(poly) == 1 for poly in feature["geometry"]["coordinates"])


def test_kenya_counties_geojson(tmp_path, capsys, resolve):
    path = tmp_path / "counties.json"
    run_skill(resolve, "KEN", "--counties-geojson", str(path))
    assert "Wrote county boundaries" in capsys.readouterr().err
    fc = json.loads(path.read_text())
    assert len(fc["features"]) == 47


def test_counties_only_for_kenya(tmp_path, resolve, capsys):
    with pytest.raises(SystemExit) as exc:
        run_skill(resolve, "Tana basin", "--counties-geojson", str(tmp_path / "c.json"))
    assert exc.value.code == 2
    assert "only Kenya" in capsys.readouterr().err


@pytest.mark.parametrize("alias", ["Kenya", "KEN", "Kenya KNSDI", "Kenya counties"])
def test_kenya_aliases(mod, alias):
    assert mod.load(alias)[0] == "kenya"


@pytest.mark.parametrize(
    "alias",
    [
        "Kenya OND region",
        "Kenya OND",
        "OND Kenya",
        "Kenya OND box",
        "Kenya OND briefing box",
        "Central-Eastern Kenya",
        "Central and Eastern Kenya",
        "CE Kenya",
    ],
)
def test_kenya_ond_aliases_print_box(capsys, resolve, alias):
    run_skill(resolve, alias)
    assert capsys.readouterr().out.strip() == "5.0/36.5/-5.0/42.0"


def test_kenya_ond_geojson_is_custom_box(tmp_path, capsys, resolve):
    path = tmp_path / "ond.json"
    run_skill(resolve, "Kenya OND region", "--geojson", str(path))
    (feature,) = json.loads(path.read_text())["features"]
    props = feature["properties"]
    assert props["name"] == "Kenya OND region"
    assert props["level"] == "custom"
    assert props["iso3"] == "KEN"
    assert props["bbox"] == [5.0, 36.5, -5.0, 42.0]
    assert feature["geometry"]["type"] == "Polygon"


def test_east_africa_stops_at_15s(tmp_path, capsys, resolve):
    path = tmp_path / "east_africa.json"
    run_skill(resolve, "East Africa", "--geojson", str(path))
    n, w, s, e = (float(v) for v in capsys.readouterr().out.strip().split("/"))
    assert s == pytest.approx(-15.0)
    assert n > 10 and w < 34 and e > 50
    (feature,) = json.loads(path.read_text())["features"]
    assert feature["properties"]["name"] == "East Africa"
    assert feature["properties"]["level"] == "region"
    assert feature["geometry"]["type"] == "MultiPolygon"
