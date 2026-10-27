# PyFoamDecomp

A Python toolbox for working with **decomposed OpenFOAM cases** — cases that have been split across `processor0/`, `processor1/`, … directories by `decomposePar`.

> **Early-stage project.** The current toolset covers timestep inspection, cleanup, and log parsing. More utilities for decomposed data handling are planned.

No third-party dependencies — everything runs on the Python 3 standard library.

---

## Tools

### `foamTimes.py` — timestep management

List, inspect, and delete timesteps across all `processor*` directories, or in the case root for undecomposed cases.

Timestep discovery uses the **union** across processors, so a timestep is considered present if *any* processor wrote it. This makes the tool safe to run even after a partial write (e.g. a crashed simulation).

If the case has no `processor*` directories, the time directories in the case root are used instead. In a decomposed case, root time directories (e.g. from `reconstructPar`) are ignored unless you pass `--root`; root and processor timesteps are never mixed in one run.

```
Usage: foamTimes.py [OPTIONS] [CASE_DIR]

Arguments:
  CASE_DIR    Path to the OpenFOAM case directory (default: current directory)

Options:
  --all             Act on ALL timesteps instead of just the latest
  --time TIME, -t   Act on a specific timestep value, matched numerically
                    (e.g. --time 8.0 finds 8/; error if no match)
  --keep-last N     Act on all timesteps except the N newest complete ones
  --root            Use time directories in the case root even if processor* dirs exist
  --rm              Delete the selected timestep(s)
  --dry-run, -n     Preview what would be deleted without actually deleting
  --with-zero       Include the 0/ directory in scope
  --verbose, -v     Show per-processor breakdown for each timestep
```

#### Examples

```bash
# --- Listing ---
python3 foamTimes.py                       # latest timestep, current dir
python3 foamTimes.py /path/to/case         # latest timestep, given dir
python3 foamTimes.py --all                 # all timesteps (union across processors)
python3 foamTimes.py --all --verbose       # all timesteps + per-processor presence

# --- Deletion ---
python3 foamTimes.py --rm                  # delete latest timestep
python3 foamTimes.py --all --rm            # delete ALL timesteps (except 0/)
python3 foamTimes.py --time 1.5 --rm       # delete a specific timestep
python3 foamTimes.py --rm --dry-run        # preview deletion without executing

# --- Pruning ---
python3 foamTimes.py --keep-last 3         # show which timesteps would be kept/deleted
python3 foamTimes.py --keep-last 3 -n      # preview the deletion
python3 foamTimes.py --keep-last 3 --rm    # keep only the 3 newest complete timesteps

# --- Case root ---
python3 foamTimes.py --all /path/to/undecomposed/case   # root times used automatically
python3 foamTimes.py --root --keep-last 2 --rm          # prune reconstructed times in a decomposed case
```

`--keep-last N` counts only **complete** timesteps (present on every processor), so a partially written timestep from a crash never takes one of the N slots. Partial timesteps newer than the oldest kept one are also kept (and flagged `(partial)` in the listing); everything older is deleted. If there are N or fewer complete timesteps, nothing is deleted. `--all`, `--time` and `--keep-last` are mutually exclusive.

#### Supported processor directory layouts

| Pattern | Example | Source |
|---|---|---|
| `processorN` | `processor0` | Classic OpenFOAM |
| `processorsN_start-end` | `processors4_0-3` | Newer OpenFOAM collated I/O |

---

### `foamLogParse.py` — log file parser

Parse an OpenFOAM solver log and extract per-timestep quantities into
whitespace-separated `.txt` files for plotting with numpy, pandas, gnuplot, etc.

```
Usage: foamLogParse.py [OPTIONS] LOGFILE

Arguments:
  LOGFILE     Path to the OpenFOAM log file

Options:
  --out DIR, -o    Output directory (default: foamLog_<logname>/ next to the log)
  --prefix STR     String prepended to every output file name (e.g. "run1_")
  --no-courant     Skip writing courant.txt
  --no-residuals   Skip writing per-field residual files
  --no-continuity  Skip writing continuity.txt
  --no-timing      Skip writing timing.txt
  --verbose, -v    Print a summary table after parsing
```

#### Output files

| File | Columns |
|---|---|
| `courant.txt` | `Time  deltaT  Co_mean  Co_max` |
| `continuity.txt` | `Time  cont_sum_local  cont_global  cont_cumulative` |
| `timing.txt` | `Time  ExecutionTime  ClockTime` |
| `residuals_<field>.txt` | `Time  InitResid  FinalResid  nIter` |

One `residuals_<field>.txt` is written per solver field (`Ux`, `Uy`, `Uz`, `p`, …). Fields are discovered dynamically from the log — no hard-coded solver assumptions — so the tool works with any OpenFOAM solver (rheoFoam, interFoam, simpleFoam, buoyantFoam, …).

All output files have `#`-commented headers and use double-space separation, making them directly usable as:

```python
import numpy as np
co  = np.loadtxt('foamLog_log/courant.txt',    comments='#')
res = np.loadtxt('foamLog_log/residuals_p.txt', comments='#')
```

#### Examples

```bash
# Parse a log, write output next to it in foamLog_log/
python3 foamLogParse.py log.out

# Write to a specific directory with a run prefix
python3 foamLogParse.py log.out --out results/ --prefix run1_

# Parse and print a summary table
python3 foamLogParse.py log.out --verbose

# Only write Courant and residuals, skip the rest
python3 foamLogParse.py log.out --no-continuity --no-timing
```

#### Notes

- **Multi-corrector fields** (e.g. pressure in PISO/PIMPLE loops, solved multiple times per step): records the *first* initial residual, the *last* final residual, and the *total* iteration count across all corrector steps.
- **Coupled solvers** (e.g. rheoTool's `Petsc:preonly:lu: Solving coupled for p, ...`) are parsed like segregated ones, into the same `residuals_<field>.txt` files.
- **Safe to run on a live log** — the last in-progress time step is flushed at EOF, so you can parse while the simulation is still running.
- **Parallel logs** — opened with `errors='replace'` to survive garbled output from MPI ranks writing concurrently.

---

## Requirements

- Python ≥ 3.6
- Standard library only (`os`, `re`, `argparse`, `pathlib`, `shutil`)
