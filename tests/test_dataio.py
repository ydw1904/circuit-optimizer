"""The loader's contract: accept a clean file whole, reject anything else whole."""

import csv
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook

from circuit_optimizer import dataio
from circuit_optimizer.dataio import DataError

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
ROUTE_HEADER = ("iata,name,distance_km,category,eco_demand,bus_demand,"
                "first_demand,cargo_demand,route_price")
GOOD = "EXA,Example A,5000,2,4500,300,40,500,0"
AIRCRAFT_HEADER = "model,category,speed_kmh,range_km,max_pax,max_tonnage,price"


def put(tmp_path, *lines, name="routes.csv", header=ROUTE_HEADER):
    p = tmp_path / name
    p.write_text("\n".join([header, *lines]) + "\n", encoding="utf-8")
    return p


def xlsx(tmp_path, rows, name="routes.xlsx"):
    wb = Workbook()
    for row in rows:
        wb.active.append(row)
    p = tmp_path / name
    wb.save(p)
    return p


def test_example_files_load():
    routes = dataio.load_routes(EXAMPLES / "routes.csv")
    fleet = dataio.load_aircraft(EXAMPLES / "aircraft.csv")
    assert len(routes) == 40 and len(fleet) == 3
    assert set(routes[0]) == {"iata", "name", "dist", "cat", "eco_d", "bus_d",
                              "fir_d", "cargo_d", "price"}
    assert set(fleet[0]) == {"model", "alias", "cat", "speed", "range", "pax",
                             "tonnage", "price"}


def test_xlsx_and_csv_give_the_same_rows(tmp_path):
    with open(EXAMPLES / "routes.csv", newline="") as f:
        table = [[c if i == 0 or j < 2 else int(c) for j, c in enumerate(row)]
                 for i, row in enumerate(csv.reader(f))]
    assert dataio.load_routes(xlsx(tmp_path, table)) == dataio.load_routes(EXAMPLES / "routes.csv")


def test_tolerated_quirks(tmp_path):
    # UTF-8 BOM, semicolons (German Excel), reordered columns, lowercase code,
    # whole-number float, blank padding rows.
    p = tmp_path / "routes.csv"
    p.write_text("﻿name;iata;distance_km;category;route_price;eco_demand;"
                 "bus_demand;first_demand;cargo_demand\n"
                 "Example A;exa;5000;2;0;4500.0;300;40;500\n;;;;;;;;\n\n",
                 encoding="utf-8")
    [route] = dataio.load_routes(p)
    assert route["iata"] == "EXA" and route["eco_d"] == 4500 and route["dist"] == 5000


def test_xlsx_ignores_blank_padding_and_trailing_header_cells(tmp_path):
    header = ROUTE_HEADER.split(",") + [None, None]
    row = ["EXA", "Example A", 5000, 2, 4500, 300, 40, 500, 0, None, None]
    assert len(dataio.load_routes(xlsx(tmp_path, [header, row, [None] * 11, [None] * 11]))) == 1


BAD_ROUTE_ROWS = [
    ("EXA,Example A,5000,2,,300,40,500,0", "'eco_demand' is empty"),
    ("EXA,Example A,5000,2,abc,300,40,500,0", "plain number"),
    ('EXA,Example A,5000,2,"4,500",300,40,500,0', "plain number"),
    ("EXA,Example A,5000,2,nan,300,40,500,0", "plain number"),
    ("EXA,Example A,5000,2,1e3,300,40,500,0", "plain number"),
    ("EXA,Example A,-5,2,4500,300,40,500,0", "between"),
    ("EXA,Example A,5000,2,0,300,40,500,0", "'eco_demand' must be between"),
    ("EXA,Example A,5000,2,4500.5,300,40,500,0", "whole number"),
    ("EX1,Example A,5000,2,4500,300,40,500,0", "3 letters"),
    ("EXAB,Example A,5000,2,4500,300,40,500,0", "3 letters"),
    ("EXA,Example A,5000,2,4500,300,40,500,0,extra", "to the right"),
    ("EXA,Example A,5000,2,4500,300,40", "'cargo_demand' is empty"),
    ("EXA,Bad\x07Name,5000,2,4500,300,40,500,0", "printable text"),
    ("EXA,Example A,5000,2,99999999,300,40,500,0", "between"),
]


@pytest.mark.parametrize("line,fragment", BAD_ROUTE_ROWS)
def test_bad_route_row_rejects_the_whole_file(tmp_path, line, fragment):
    p = put(tmp_path, GOOD.replace("EXA", "EXZ"), line, GOOD.replace("EXA", "EXY"))
    with pytest.raises(DataError) as e:
        dataio.load_routes(p)
    assert fragment in str(e.value)
    assert "routes.csv row 3" in str(e.value)  # points at the offending row


@pytest.mark.parametrize("header,fragment", [
    (ROUTE_HEADER.replace(",route_price", ""), "missing column(s) route_price"),
    (ROUTE_HEADER + ",notes", "unknown column(s) notes"),
    (ROUTE_HEADER + ",iata", "duplicated column(s) iata"),
    (ROUTE_HEADER.replace("name", "city"), "unknown column(s) city"),
    ("", "file is empty"),
])
def test_bad_header(tmp_path, header, fragment):
    p = tmp_path / "routes.csv"
    p.write_text(header + ("\n" + GOOD if header else ""), encoding="utf-8")
    with pytest.raises(DataError) as e:
        dataio.load_routes(p)
    assert fragment in str(e.value)


def test_duplicate_destination(tmp_path):
    with pytest.raises(DataError, match=r"row 3: duplicate iata 'EXA' \(first on row 2\)"):
        dataio.load_routes(put(tmp_path, GOOD, GOOD.lower()))


def test_header_only_file(tmp_path):
    with pytest.raises(DataError, match="no data rows"):
        dataio.load_routes(put(tmp_path))


def test_file_level_rejections(tmp_path, monkeypatch):
    with pytest.raises(DataError, match="not found"):
        dataio.load_routes(tmp_path / "nope.csv")
    with pytest.raises(DataError, match="unsupported file type"):
        dataio.load_routes(put(tmp_path, GOOD, name="routes.xls"))
    (tmp_path / "bin.csv").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(DataError, match="cannot be read"):
        dataio.load_routes(tmp_path / "bin.csv")
    (tmp_path / "junk.xlsx").write_bytes(b"this is not a zip")
    with pytest.raises(DataError, match="cannot be read"):
        dataio.load_routes(tmp_path / "junk.xlsx")
    big = put(tmp_path, GOOD, name="big.csv")
    monkeypatch.setattr(dataio, "MAX_BYTES", 10)
    with pytest.raises(DataError, match="larger than"):
        dataio.load_routes(big)
    monkeypatch.setattr(dataio, "MAX_BYTES", 5_000_000)
    monkeypatch.setattr(dataio, "MAX_ROWS", 2)
    with pytest.raises(DataError, match="more than 2 rows"):
        dataio.load_routes(put(tmp_path, GOOD, GOOD.replace("EXA", "EXB"), GOOD.replace("EXA", "EXC")))


@pytest.mark.parametrize("cell", [True, date(2026, 1, 1), "4,500", float("inf")])
def test_xlsx_cells_that_only_look_like_numbers(tmp_path, cell):
    row = ["EXA", "Example A", 5000, 2, cell, 300, 40, 500, 0]
    with pytest.raises(DataError, match="eco_demand"):
        dataio.load_routes(xlsx(tmp_path, [ROUTE_HEADER.split(","), row]))


def test_xlsx_formula_without_cached_value_is_empty(tmp_path):
    row = ["EXA", "Example A", 5000, 2, "=1+1", 300, 40, 500, 0]  # saved by openpyxl: no cache
    with pytest.raises(DataError, match="'eco_demand' is empty"):
        dataio.load_routes(xlsx(tmp_path, [ROUTE_HEADER.split(","), row]))


def test_cross_field_hook_rejects_with_its_message(tmp_path, monkeypatch):
    monkeypatch.setattr(dataio, "_route_problem", lambda r: "no thanks" if r["iata"] == "EXB" else None)
    p = put(tmp_path, GOOD, GOOD.replace("EXA", "EXB"))
    with pytest.raises(DataError, match="routes.csv row 3: no thanks"):
        dataio.load_routes(p)


def test_aircraft_rules(tmp_path):
    ok = "DEMO-S,1,800,9000,190,18.5,45000000"
    [ac] = dataio.load_aircraft(put(tmp_path, ok, name="a.csv", header=AIRCRAFT_HEADER))
    assert ac["tonnage"] == 18.5 and ac["alias"] == "DEMO-S" and ac["speed"] == 800
    for line, fragment in [
        ("DEMO-S,1,0,9000,190,18.5,1", "'speed_kmh' must be between"),   # would divide by zero
        ("DEMO-S,1,800,9000,190,0,1", "'max_tonnage' must be between"),
        ("DEMO-S,1,800,9000,190.5,18.5,1", "whole number"),
    ]:
        with pytest.raises(DataError, match=fragment):
            dataio.load_aircraft(put(tmp_path, line, name="a.csv", header=AIRCRAFT_HEADER))
    with pytest.raises(DataError, match="duplicate model"):
        dataio.load_aircraft(put(tmp_path, ok, ok.lower(), name="a.csv", header=AIRCRAFT_HEADER))
