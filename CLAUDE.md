# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Coding guidelines

- Don't write comments unless they're absolutely necessary to explain genuinely non-obvious logic (in which case, keep them concise and clear), or a TODO/FIXME with a concrete reason. Don't write comments that just restate what the code does — that's AI slop, and it's a DRY violation: it's now a second place to update when the logic changes.
- Don't assume existing code is correct or well-structured — call out bad patterns and propose better ones rather than copying them forward.
- Prefer readable, intuitive code over clever abstractions. No speculative flexibility/configurability that wasn't asked for.
- When adding a feature or fixing a bug, add tests that actually fail if the behavior regresses — not tests that only exercise structure or pad coverage.
- Keep commit messages concise: a brief one-line summary, then one paragraph (max) on the additions.
- Never put a Claude Code session id/link (e.g. a `Claude-Session:` trailer) in a commit message or PR description — strip it if the harness auto-appends one. A `Co-Authored-By: Claude ...` trailer is fine to keep.

## Think before coding

Don't assume, don't hide confusion, surface tradeoffs. Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple reasonable interpretations exist, present them — don't pick one silently.
- If a simpler approach exists than the one implied by the request, say so.
- If something is unclear, stop and name what's confusing rather than guessing.

## Project overview

PyFoamDecomp is a collection of standalone Python 3 CLI scripts for working with **decomposed OpenFOAM cases** (cases split into `processor*/` directories by `decomposePar`) and their solver logs. It is early-stage: currently `foamTimes.py` (timestep list/delete in the case root and/or processor dirs, classic or collated) and `foamLogParse.py` (log -> plottable `.txt` tables).

Hard constraint: **standard library only** (no numpy, pandas, etc.), Python >= 3.6. Keep new scripts dependency-free.

## Running

There is no build step, packaging, or linter config. Each tool is a single executable script run directly:

```bash
python3 foamTimes.py [--all|--time T|--keep-last N] [--root] [--decomposed] [--rm] [--dry-run] [--with-zero] [-v] [CASE_DIR]
python3 foamLogParse.py LOGFILE [--out DIR] [--prefix STR] [--no-courant|--no-residuals|--no-continuity|--no-timing] [-v]
```

### Tests

Stdlib `unittest`, building throwaway `processorN/<time>/` trees and synthetic logs in temp dirs (`tests/test_foamTimes.py`, `tests/test_foamLogParse.py`):

```bash
python3 -m unittest discover tests
```

### Manual testing

`local_testing/` is gitignored and holds local test data. `local_testing/data/log.out` is a real ~2.8 MB OpenFOAM-9 `rheoFoam` parallel log (768 procs) -- use it to exercise `foamLogParse.py`:

```bash
python3 foamLogParse.py local_testing/data/log.out --out <scratchpad dir> -v
```

Pass `--out` so output does not land in `local_testing/data/foamLog_log/`. `local_testing/of-test-data/` has small real rheoTool cases: `01-unDecomposed` (root time dirs), `02-decomposed` (4 `processorN/` dirs), `03-decomposed_collated_fh` (collated `processors4/`, an empty `processor0/`, and reconstructed root times). Run `foamTimes.py` against scratchpad copies of them, never the originals, and always try `--dry-run` before `--rm`.

## Architecture and conventions

Both scripts share the same layout, and new tools should follow it:

1. Module docstring containing usage/examples -- it is reused as the argparse `epilog` (`foamTimes.py` slices it at `'Usage:'`).
2. Section banners (`# ----` comment blocks): core helpers / parser -> actions / writers -> CLI.
3. `build_parser()` returns the `ArgumentParser`; `main()` resolves paths, validates, dispatches; `if __name__ == '__main__': main()`.
4. Errors go to stderr followed by a non-zero exit (`sys.exit(...)`).

### foamTimes.py

- Every helper takes `sides`: a list of "sides", each a list of dirs holding time dirs -- `[case_dir]` (root) or the processor dirs of one family. `resolve_sides` picks them from `--root`/`--decomposed`: with neither, listing uses every side with times, while deleting errors if both sides have times, and never falls back to the root when processor dirs exist (pass `--root`). A root with only `0/` has no times unless `--with-zero`.
- Processor dirs are grouped into families (`find_processor_families`): classic `processorN` is one, collated `processorsN`/`processorsN_a-b` is one per N. Families without time dirs are ignored (collated runs often have an empty `processor0/`); more than one family with times is an error.
- Processor dirs matched by `PROCESSOR_DIR_RE`; bare `processors/` (old OF versions) is not supported.
- Timestep dirs matched by a numeric/scientific regex (`is_time_dir`), sorted by `float` value, not lexically.
- Timesteps are the **union** across all locations of the selected sides, so partially written (crashed) timesteps are still listed and deletable; deletion silently skips processors lacking that time.
- `--time` matches by numeric value (`find_time_by_value`) and acts on the real dir name in every selected side; no match is an error.
- `0/` is excluded unless `--with-zero`. `--dry-run` alone implies the delete path (prints only).
- `--keep-last N` counts only complete timesteps (`is_complete`: in every location of some selected side) toward N; partial timesteps newer than the oldest kept one are kept too, and nothing is deleted with N or fewer complete timesteps (`split_keep_last`).

### foamLogParse.py

- `parse_log()` is a single-pass streaming state machine over the log. Key ordering facts it relies on:
  - `Courant Number ...` and `deltaT = ...` lines appear **before** `Time = T`, so they are buffered in `pending_*` and attached when the `Time =` line arrives.
  - A `Time =` line flushes the previous step's record; the last step is flushed at EOF (makes it safe on a live log).
  - Lines before the first `Time =` are ignored (the log header's `Time   : hh:mm:ss` uses a colon and does not match `^Time\s*=`).
- Solver fields are discovered dynamically from `Solving for <field>` and `Solving coupled for <field>` (rheoTool coupled p-U) lines (no solver-specific assumptions); coupled and segregated solves share the same per-field record. For fields solved multiple times per step (PISO/PIMPLE correctors): keep the **first** initial residual, the **last** final residual, and **sum** iterations.
- `RE_COURANT` uses `.search` (so `Interface Courant Number` matches); the other regexes use `.match` anchored at line start.
- Output: `#`-commented header, values formatted `{:.10e}` joined by two spaces, missing values as `NaN` -- intended for `np.loadtxt(..., comments='#')`. Keep this format stable when adding outputs.
- Logs are opened with `errors='replace'` to tolerate garbled interleaved MPI output.

Style note: `foamTimes.py` uses `str.format`, `foamLogParse.py` uses f-strings; match the file you are editing.

## Direction

`roadmap.txt` (gitignored, personal notes) lists planned tools: `foamFields`, `foamClean`, `foamPostProc` (postProcessing/ functionObject output with restart stitching), `foamFieldStats`, `foamReconstructHelper`, `foamCheckDecomp`, `foamDiskUsage`, `foamBoundaryInfo`, and others. Planned infrastructure: once more scripts exist, factor duplicated helpers (processor-dir discovery, timestep scanning, numeric sort key) into a shared `pyfoamdecomp/` package and add `pyproject.toml` console entry points. Until then, scripts are self-contained and duplicate helpers rather than importing each other. New `--rm`-style destructive features should offer `--dry-run`, consistent with `foamTimes.py`.

When adding or changing a tool, update its section in `README.md` (usage block, options, examples).
