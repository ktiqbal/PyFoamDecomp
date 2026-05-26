# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

**PyFoamDecomp** is a growing Python toolbox for working with **decomposed OpenFOAM cases** — cases split across `processor0/`, `processor1/`, … directories by `decomposePar`. The goal is a collection of focused, dependency-free CLI utilities that cover the common pain points of working with decomposed data: inspecting timesteps, managing fields, checking decomposition consistency, parsing logs, and wrapping reconstruction workflows.

The project is in early stages. Current tools are `foamTimes.py` and `foamLogParse.py`. See `roadmap.txt` (untracked) for planned additions.

All scripts use Python stdlib only — no pip install needed.

## Repo layout

```
foamTimes.py      # timestep listing / deletion across processor* dirs
foamLogParse.py   # log file parser: Courant numbers, residuals, continuity, timing
roadmap.txt       # untracked personal notes on planned utilities
```

As new utilities are added, shared helpers (processor-dir discovery, timestep scanning, numeric sorting) should be factored into a common module rather than copied across scripts.

## Running the scripts

### foamTimes.py

```bash
python3 foamTimes.py                       # list latest timestep, current dir
python3 foamTimes.py /path/to/case         # list latest timestep, given case dir
python3 foamTimes.py --all                 # list all timesteps (union across processors)
python3 foamTimes.py --rm                  # delete latest timestep
python3 foamTimes.py --all --rm            # delete ALL timesteps (except 0/)
python3 foamTimes.py --time 1.5 --rm       # delete a specific timestep
python3 foamTimes.py --rm --dry-run        # preview deletion without executing
python3 foamTimes.py --verbose             # show per-processor breakdown
python3 foamTimes.py --with-zero           # include the 0/ directory in scope
```

## Running the scripts (continued)

### foamLogParse.py

```bash
python3 foamLogParse.py log.out                    # parse, write to foamLog_log/ next to the log
python3 foamLogParse.py log.out --out results/     # write to a specific directory
python3 foamLogParse.py log.out --prefix run1_     # prefix all output files (e.g. run1_courant.txt)
python3 foamLogParse.py log.out --verbose          # also print a summary table
python3 foamLogParse.py log.out --no-continuity    # skip continuity.txt
python3 foamLogParse.py log.out --no-residuals     # skip per-field residual files
python3 foamLogParse.py log.out --no-timing        # skip timing.txt
python3 foamLogParse.py log.out --no-courant       # skip courant.txt
```

Output files (whitespace-separated, `#`-commented headers for numpy/pandas/gnuplot):

| File | Columns |
|---|---|
| `courant.txt` | `Time  deltaT  Co_mean  Co_max` |
| `continuity.txt` | `Time  cont_sum_local  cont_global  cont_cumulative` |
| `timing.txt` | `Time  ExecutionTime  ClockTime` |
| `residuals_<field>.txt` | `Time  InitResid  FinalResid  nIter` |

Fields are discovered dynamically from the log. When a field is solved multiple times per step (e.g. pressure in PISO/PIMPLE corrector loops), the **first initial residual** and **last final residual** are recorded and iteration counts are summed.

## Architecture

### foamTimes.py

- **Core helpers** — `is_time_dir`, `time_sort_key`, `find_processor_dirs`, `get_times_union`, `get_latest_time`: filesystem scanning and numeric sorting of OpenFOAM timestep directories.
- **Actions** — `list_times`, `delete_times`: operate on the resolved list of timesteps; `delete_times` silently skips processors missing a given timestep (safe for partial writes).
- **CLI** — `build_parser` / `main`: argparse wiring that translates `--all`, `--time`, `--rm`, `--dry-run`, `--verbose`, `--with-zero` into calls to the action functions.

### foamLogParse.py

- **Single-pass streaming parser** — reads line-by-line; handles logs larger than memory.
- **State machine** — `pending_co_mean/co_max/delta_t` are buffered when the Courant/deltaT lines appear, then committed when the `Time = T` line arrives (OpenFOAM prints them in that order).
- **Multi-corrector awareness** — for fields solved N times per step (PISO pressure correctors, PIMPLE outer iterations), tracks first `init` residual, last `final` residual, and summed `nIter`.
- **Tolerant of mid-run logs** — the last in-progress step is flushed after EOF.
- **Dynamic field discovery** — no hard-coded field list; works with any solver (rheoFoam, interFoam, simpleFoam, …).
- **`errors='replace'` file open** — survives garbled output in parallel logs.

### Design principles (apply to all future tools)

- **Union semantics for timestep discovery** — a timestep is considered present if *any* processor wrote it. Protects against partial writes mid-simulation.
- **Silent skip on missing dirs** — if a processor is missing an expected timestep, skip it without error (partial write tolerance).
- **Processor directory regex**: `processors?[0-9]+(_[0-9]+-[0-9]+)?` — covers both `processorN` (classic) and `processorsN_start-end` (newer collated I/O) layouts.
- **No third-party dependencies** — stdlib only.
