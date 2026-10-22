import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from circuit_optimizer import planner

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
ROUTES, AIRCRAFT = str(EXAMPLES / "routes.csv"), str(EXAMPLES / "aircraft.csv")


def test_plan_reranks_every_aircraft_candidate_with_phase2(monkeypatch):
    aircraft = {
        alias: {
            "alias": alias, "model": alias, "cat": 1, "speed": 700,
            "range": 20_000, "pax": 100, "tonnage": 10, "price": 1,
        }
        for alias in ("AC1", "AC2")
    }
    routes = {
        alias: [{
            "iata": f"{letter}{index}", "name": f"Route {letter}{index}",
            "dist": 1_000, "ft": 10, "eco_d": 1_000, "bus_d": 100,
            "fir_d": 10, "cargo_d": 100, "price": 1,
        } for letter in letters for index in range(2)]
        for alias, letters in (("AC1", "AB"), ("AC2", "CD"))
    }
    phase1 = {
        "AC1": [(100, 20, (0, 1)), (90, 20, (2, 3))],
        "AC2": [(95, 20, (0, 1)), (80, 20, (2, 3))],
    }
    revenue = {"A0": 100, "B0": 300, "C0": 200, "D0": 400}
    evaluated = []

    monkeypatch.setattr(planner.dataio, "load_routes", lambda _path: [])
    monkeypatch.setattr(planner.dataio, "load_aircraft", lambda _path: list(aircraft.values()))
    monkeypatch.setattr(planner, "eligible_routes",
                        lambda _all, ac, **_kwargs: routes[ac["alias"]])
    monkeypatch.setattr(
        planner, "search_circuits",
        lambda _routes, ac, _comfort, _speed, **kwargs: (
            phase1[ac["alias"]] if kwargs["match"] == 0.9 else []),
    )

    def optimize(chosen, ac, **kwargs):
        evaluated.append((ac["alias"], chosen[0]["iata"], kwargs["wave_slack"]))
        return ({"eco": 100, "bus": 0, "fir": 0, "cargo": 0}, 1,
                revenue[chosen[0]["iata"]], [])

    monkeypatch.setattr(planner, "optimize_circuit", optimize)
    monkeypatch.setattr(planner, "print_circuit", lambda *_args, **_kwargs: None)

    result = planner._plan(SimpleNamespace(
        routes="r", aircraft="a", only=None, circuits=1, exclude=[],
        comfort=500, speed=700, max_waves=30, match=0.9,
        min_dist=None, max_dist=None, max_routes=150,
        candidates_per_ac=2, beam=1200, overshoot=0.0,
        phase1_only=False, wave_slack=0.02,
        bulk_discount=0.0, bulk_threshold=63,
    ))

    assert evaluated == [
        ("AC1", "A0", 0.02), ("AC1", "B0", 0.02),
        ("AC2", "C0", 0.02), ("AC2", "D0", 0.02),
    ]
    assert result["circuits"][0]["aircraft"] == "AC2"
    assert [route["iata"] for route in result["circuits"][0]["routes"]] == ["D0", "D1"]
    assert result["circuits"][0]["daily_rev"] == 400


def test_eligible_routes_filters_and_orders():
    ac = {"cat": 2, "speed": 900, "range": 10_000}
    r = lambda iata, dist, cat: {"iata": iata, "dist": dist, "cat": cat}
    routes = [r("AAA", 3000, 2), r("BBB", 9000, 3), r("CCC", 12_000, 3),   # CCC out of range
              r("DDD", 4000, 1), r("EEE", 6000, 2), r("FFF", 500, 5)]      # DDD category too low
    got = planner.eligible_routes(routes, ac, exclude_iatas=["eee"])
    assert [x["iata"] for x in got] == ["BBB", "AAA", "FFF"]               # longest first, EEE excluded
    assert all("ft" in x for x in got)
    got = planner.eligible_routes(routes, ac, min_dist=1000, max_dist=8000)
    assert [x["iata"] for x in got] == ["EEE", "AAA"]                      # BBB/CCC too far, FFF too near
    slow = {"cat": 1, "speed": 100, "range": 20_000}                       # 168h cap: 10000 km at 100 km/h
    assert planner.eligible_routes([r("GGG", 10_000, 1)], slow) == []


def run_json(capsys, *extra):
    planner.main(["--routes", ROUTES, "--aircraft", AIRCRAFT, "--json", "--beam", "300", *extra])
    return json.loads(capsys.readouterr().out)


def test_end_to_end_on_the_example_files(capsys):
    doc = run_json(capsys, "--circuits", "3")
    assert 1 <= len(doc["circuits"]) <= 3
    seen = set()
    for c in doc["circuits"]:
        iatas = {r["iata"] for r in c["routes"]}
        assert not iatas & seen, "a route was used by two circuits"
        seen |= iatas
        assert c["total_hours"] <= 168 and c["waves"] >= 1 and c["daily_rev"] > 0
        assert c["cfg"] and c["investment"] > 0


def test_only_restricts_the_fleet_and_exclude_removes_routes(capsys):
    doc = run_json(capsys, "--circuits", "2", "--only", "demo-l", "--exclude", "EYD", "exe")
    assert {c["aircraft"] for c in doc["circuits"]} == {"DEMO-L"}
    assert not {"EYD", "EXE"} & {r["iata"] for c in doc["circuits"] for r in c["routes"]}


def test_unknown_model_in_only_is_an_error():
    with pytest.raises(SystemExit, match="NOPE"):
        planner.main(["--routes", ROUTES, "--aircraft", AIRCRAFT, "--only", "NOPE"])


def test_malformed_file_is_rejected_before_any_output(tmp_path, capsys):
    bad = tmp_path / "routes.csv"
    bad.write_text("iata,name\nEXA,Example A\n")
    with pytest.raises(SystemExit) as e:
        planner.main(["--routes", str(bad), "--aircraft", AIRCRAFT])
    out = capsys.readouterr()
    assert e.value.code == 2
    assert out.out == "" and out.err.startswith("REJECTED: routes.csv header: missing column(s)")
