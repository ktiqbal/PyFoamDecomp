# PyFoamDecomp

A Python toolbox for working with **decomposed OpenFOAM cases** — cases that have been split across `processor0/`, `processor1/`, … directories by `decomposePar`.

> **Early-stage project.** The current toolset covers timestep inspection and cleanup. More utilities for decomposed data handling are planned.

No third-party dependencies — everything runs on the Python 3 standard library.

---

## Tools

### `foamTimes.py` — timestep management

List, inspect, and delete timesteps across all `processor*` directories.

Timestep discovery uses the **union** across processors, so a timestep is considered present if *any* processor wrote it. This makes the tool safe to run even after a partial write (e.g. a crashed simulation).

```
Usage: foamTimes.py [OPTIONS] [CASE_DIR]

Arguments:
  CASE_DIR    Path to the OpenFOAM case directory (default: current directory)

Options:
  --all             Act on ALL timesteps instead of just the latest
  --time TIME, -t   Act on a specific timestep value (e.g. --time 1.5)
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
```

#### Supported processor directory layouts

| Pattern | Example | Source |
|---|---|---|
| `processorN` | `processor0` | Classic OpenFOAM |
| `processorsN_start-end` | `processors4_0-3` | Newer OpenFOAM collated I/O |

---

## Requirements

- Python ≥ 3.6
- Standard library only (`pathlib`, `argparse`, `re`, `shutil`)
