# circuit-optimizer

A revenue optimiser for [Airlines Manager](https://www.airlines-manager.com) circuits. Give it a
spreadsheet of candidate routes from one hub and a spreadsheet of aircraft. It returns the route
sets, the one seat configuration shared by every leg, how many aircraft to buy, and how fast they
pay for themselves.

It is a calculator and nothing more. It reads two files from your disk, prints a plan, and never
talks to the game or to any website. It ships no game data: you bring your own.

> Unofficial fan project, not affiliated with or endorsed by the game's publisher. You are
> responsible for following the game's terms when you collect your data.

```
Circuit #1  |  DEMO-L
Time: 167.75h / 168h (0.25h gap)  |  Rotations: 1.00
Routes: 9  |  Config: eco=366  bus=22  fir=2  cargo=44
Constraints: seats=414/720  payload=83.7/92.5T
Waves: 6 (42 aircraft)  |  Daily: $170,235,360  |  Weekly: $1,191,647,520
Payback:  76.5 days  (2.5 months)
Demand ratio (min/max) - eco:0.91  bus:0.69  fir:0.45  cargo:0.92
```

*(From the synthetic data in `examples/`, which plans three circuits in about a second.)*

## The problem

A **circuit** is a set of routes that one aircraft flies back to back in a week, starting and ending
at the hub. Flights are scheduled in **waves** of 7 aircraft staggered Monday to Sunday, so every
route in the circuit gets one round trip per day from each wave. That gives these rules:

- the round trips in a circuit must fit in 168 hours,
- every leg flies the **same** seat configuration (it is the same aircraft),
- daily capacity on each route is `2 × seats × waves` per class.

Picking the routes is the hard part. Any route set that fits in the week is valid, but most of them
waste money. If one leg has 4,900 economy passengers a day and another has 3,900, a single seat
configuration can't serve both. Either the small route ends up with empty seats or the big route
is short of capacity.

### Why closing the gap with price is a bad trade

The game lets you raise the ticket price on an undersupplied route. Demand falls linearly and
reaches zero at 1.33 times the ideal price. Let `x = capacity / demand < 1`. The price that exactly
fills the seats is `ideal × (1 + (1 − x) / 3)`, and compared with a perfectly sized route you lose

```
loss = (1 − x)(3 − x) / 3
```

At `x = 0.9` you are 10% short on seats and lose 7% of revenue, so raising the price wins back
only about a third of the shortfall. Seat count is far more valuable than price. The planner
therefore looks for **routes with similar demand**, so one configuration fits all of them near
their ideal price. It doesn't pick routes first and repair the mismatch with pricing afterwards.

## How it works

The search runs in two phases, once per aircraft model, and repeats per circuit. Once a circuit is
chosen its routes are locked, so the next circuit can't reuse them.

**Phase 1: route sets (beam search).** It starts from single routes and adds one route per step,
up to 12 routes. Each step keeps the best 1,200 partial sets (`--beam`). A set survives only if:

- it still fits within 168 hours,
- its economy and cargo demand stay similar: the smallest leg's demand is at least 90% of the
  largest (`--match 0.9`).

Business and first class aren't filtered. They earn far less, and Phase 2 can give them zero
seats. Partial sets are ranked by a quick revenue estimate.

**Phase 2: seat configuration (grid search).** For each of the top candidate sets, it tries a grid
of business, first and cargo seat counts. For each grid point, it tries economy counts that land
exactly on a wave target, so a fraction of a wave's demand isn't left unserved. Each configuration
gets a wave count:

```
waves = floor( min over every leg and class of  demand / (2 × seats) )
```

The tightest leg and class set the wave count. Economy is never oversupplied. Business, first and
cargo may run up to `--overshoot` above demand, so economy can fly an extra wave. Each leg is then
priced at the price that fills it, and revenue is summed. Among configurations within 2% of the
best revenue (`--wave-slack`), the one with the fewest waves wins, because half the aircraft for
98% of the money is usually the better buy.

The report then turns revenue into an investment view: aircraft and route costs, payback in days
and yearly return.

### Three engines, one answer

Phase 1 and Phase 2 have three implementations with the same scoring rules:

|Engine|Needs|3 circuits from `examples/`|
|-|-|-|
|Rust core (`native/beam_search.rs`)|`rustc`, nothing from crates.io|**1.0 s**|
|Python + numba JIT (`[fast]`)|`numba`|63 s|
|Pure Python|nothing|76 s|

*(Wall-clock time on an Apple Silicon Mac, including start-up and the JIT warm-up.)*

The Rust core is a plain C-ABI `cdylib` loaded through `ctypes`, with no PyO3 or build system.
Route sets are 192-bit bitsets. Tests check both phases against a stored reference output, byte
for byte, so the Rust and Python versions can't drift apart. Any score that goes through `floor`
is sensitive to rounding, so the build relies on Rust never fusing multiply-adds, which keeps
results reproducible.

## Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[fast]"
sh circuit_optimizer/native/build.sh     # optional but strongly recommended
```

The planner loads the Rust core automatically once it is built, and falls back to Python when it
isn't. The install is editable (`-e`) because the library is built inside the package folder.
Tested on macOS and Linux. On Windows the Python path should work, but the build script has not
been tried.

## Run

```bash
circuit-optimizer --routes examples/routes.csv --aircraft examples/aircraft.csv --circuits 5
```

Useful options: `--only MODEL ...` (plan just some aircraft from your file), `--exclude IATA ...`,
`--min-dist` / `--max-dist` (km), `--overshoot 0.10`, `--bulk-discount 0.13 --bulk-threshold 63`,
`--json` (one JSON document on stdout, the readable report on stderr). `circuit-optimizer --help`
lists the rest.

The numbers in `examples/` are made up. Replace them with your own.

## Your data

Two files, each `.csv` (UTF-8, comma or semicolon separated) or `.xlsx` (first sheet is read).

**Routes file**: one row per candidate destination of one hub.

|Column|Meaning|Allowed|
|-|-|-|
|`iata`|destination code|3 letters|
|`name`|display name|text, up to 60 characters|
|`distance_km`|distance from the hub|whole number, 1 to 20100|
|`category`|destination category|whole number, 1 to 10|
|`eco_demand`|economy demand|whole number, at least 1|
|`bus_demand`|business demand|whole number, 0 or more|
|`first_demand`|first class demand|whole number, 0 or more|
|`cargo_demand`|cargo demand|whole number, 0 or more|
|`route_price`|cost to open the route|whole number, 0 or more (0 = unknown, left out of payback)|

**Aircraft file**: one row per model you want considered.

|Column|Meaning|Allowed|
|-|-|-|
|`model`|name you will use with `--only`|text, up to 60 characters, unique|
|`category`|aircraft category (flies routes of this category or higher)|whole number, 1 to 10|
|`speed_kmh`|cruise speed|whole number, 1 or more|
|`range_km`|maximum range|whole number, 1 or more|
|`max_pax`|seat capacity|whole number, 0 or more|
|`max_tonnage`|payload in tonnes|number, decimals allowed, above 0|
|`price`|purchase price per aircraft|whole number, 0 or more|

### Files are accepted whole or rejected whole

A plan built on one mistyped cell looks just as confident as a correct one. So every cell is
checked before anything runs, and the first problem rejects the entire file, giving the file name,
row and column:

```
REJECTED: routes.csv row 14: 'eco_demand' must be a plain number, got '4,500'
```

Rejected: missing, unknown or duplicated columns; empty cells; text where a number belongs;
thousands separators, units or scientific notation (`4,500`, `4500 km`, `1e3`); fractions in
whole-number columns; values out of range; a repeated `iata` or `model`; files with no data rows;
files over 5 MB or 10,000 rows; unreadable or corrupt files; any other file type.

Fine: any column order, upper or lower case codes, a UTF-8 BOM, semicolon separators (Excel in many
locales), blank padding rows that Excel adds.

Not checked yet: whether the demand columns make sense *together* (for example two columns pasted
in swapped order). There is a hook for this in `dataio._route_problem`.

## Develop

```bash
pip install -e ".[dev,fast]"
sh circuit_optimizer/native/build.sh
pytest
```

The native-core tests are skipped when the library isn't built. CI builds it, then runs the full
suite on Linux and macOS.

## License

MIT
