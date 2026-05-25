# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

**PyFoamDecomp** is a growing Python toolbox for working with **decomposed OpenFOAM cases** — cases split across `processor0/`, `processor1/`, … directories by `decomposePar`. The goal is a collection of focused, dependency-free CLI utilities that cover the common pain points of working with decomposed data: inspecting timesteps, managing fields, checking decomposition consistency, parsing logs, and wrapping reconstruction workflows.

The project is in early stages. The current tool is `foamTimes.py`. See `roadmap.txt` (untracked) for planned additions.

All scripts use Python stdlib only — no pip install needed.

## Repo layout

```
foamTimes.py      # timestep listing / deletion across processor* dirs
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

## Architecture

### foamTimes.py

- **Core helpers** — `is_time_dir`, `time_sort_key`, `find_processor_dirs`, `get_times_union`, `get_latest_time`: filesystem scanning and numeric sorting of OpenFOAM timestep directories.
- **Actions** — `list_times`, `delete_times`: operate on the resolved list of timesteps; `delete_times` silently skips processors missing a given timestep (safe for partial writes).
- **CLI** — `build_parser` / `main`: argparse wiring that translates `--all`, `--time`, `--rm`, `--dry-run`, `--verbose`, `--with-zero` into calls to the action functions.

### Design principles (apply to all future tools)

- **Union semantics for timestep discovery** — a timestep is considered present if *any* processor wrote it. Protects against partial writes mid-simulation.
- **Silent skip on missing dirs** — if a processor is missing an expected timestep, skip it without error (partial write tolerance).
- **Processor directory regex**: `processors?[0-9]+(_[0-9]+-[0-9]+)?` — covers both `processorN` (classic) and `processorsN_start-end` (newer collated I/O) layouts.
- **No third-party dependencies** — stdlib only.
