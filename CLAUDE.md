# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This repository contains a single Python CLI utility, `foamTimes.py`, for managing timesteps in **decomposed OpenFOAM cases** — i.e., cases that have been split into `processor0/`, `processor1/`, … directories by `decomposePar`.

## Running the Script

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

The script uses only Python stdlib (no pip install needed).

## Architecture

Everything lives in `foamTimes.py`. The structure is:

- **Core helpers** — `is_time_dir`, `time_sort_key`, `find_processor_dirs`, `get_times_union`, `get_latest_time`: filesystem scanning and numeric sorting of OpenFOAM timestep directories.
- **Actions** — `list_times`, `delete_times`: operate on the resolved list of timesteps; `delete_times` silently skips processors missing a given timestep (safe for partial writes).
- **CLI** — `build_parser` / `main`: argparse wiring that translates `--all`, `--time`, `--rm`, `--dry-run`, `--verbose`, `--with-zero` into calls to the action functions.

Key design decision: timestep discovery uses the **union** across all processor directories, so a timestep is considered present even if only one processor wrote it (protects against partial writes mid-simulation).

Processor directory matching regex: `processors?[0-9]+(_[0-9]+-[0-9]+)?` — covers both `processorN` and `processorsN_start-end` layouts produced by newer OpenFOAM versions.
