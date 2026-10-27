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

PyFoamDecomp is an installable Python 3 package of CLI tools for working with **decomposed OpenFOAM cases** (cases split into `processor*/` directories by `decomposePar`) and their solver logs. It is early-stage: currently `foamTimes` (timestep list/delete in the case root and/or processor dirs, classic or collated) and `foamLogParse` (log -> plottable `.txt` tables).

Hard constraint: **standard library only at runtime** (no numpy, pandas, etc.), Python >= 3.9. Keep new tools dependency-free; `setuptools` is only a build requirement.

## Running

`pyproject.toml` (setuptools, `src/` layout) maps each console command to a module's `main()` in `[project.scripts]`. Install into the project venv, then run the commands directly:

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/foamTimes [--all|--time T|--keep-last N] [--root] [--decomposed] [--rm] [--dry-run] [--with-zero] [-v] [CASE_DIR]
.venv/bin/foamLogParse LOGFILE [--out DIR] [--prefix STR] [--no-courant|--no-residuals|--no-continuity|--no-timing] [-v]
```

Without installing: `PYTHONPATH=src python3 -m pyfoamdecomp.times ...` (or `.logparse`). There is no linter config.

### Tests

Stdlib `unittest`, building throwaway case trees and logs in temp dirs. No install needed: `tests/ofcase.py` puts `src/` first on `sys.path`, and CLI tests run `python -m pyfoamdecomp.<tool>` with `PYTHONPATH=src`, so the suite always exercises the working tree (never a stale installed copy):

```bash
python3 -m unittest discover tests
```

- `tests/test_times.py`, `tests/test_logparse.py`: small hand-built trees/logs for specific rules. `tests/test_case.py`: direct tests of the shared `case.py` helpers. `tests/test_cli.py`: every `[project.scripts]` entry points at a module `main` whose `build_parser().prog` is the command name (needs `tomllib`, skipped below 3.11).
- `tests/ofcase.py`: fixture generator (no tests). Builds case layouts that mirror the real cases in `local_testing/of-test-data/` (undecomposed, classic `processorN/`, collated `processorsN[_a-b]/` with reconstructed root times; empty files with real names) and a synthetic parallel rheoFoam log that returns the records `parse_log` should produce. Also holds `TempDirTestCase` (fresh resolved temp dir as `self.tmp`), `run_command(module, *args)`, `times_on_disk`, `snapshot`. Add new tools' fixtures here.
- `tests/test_*_synthetic.py`: scenarios on those fixtures at realistic scale. Processor counts are `randint(8, 1024)` from `case_rng(self.id())` (`random.Random('42:<test id>')`), so each test draws the same values whether run alone or in the suite; failure messages print `n_procs` and the seed. Re-run one test with e.g. `python3 -m unittest discover tests -k test_crashed_run_rm`.

### Manual testing

`local_testing/` is gitignored and holds local test data. `local_testing/data/log.out` is a real ~2.8 MB OpenFOAM-9 `rheoFoam` parallel log (768 procs) -- use it to exercise `foamLogParse`:

```bash
foamLogParse local_testing/data/log.out --out <scratchpad dir> -v
```

Pass `--out` so output does not land in `local_testing/data/foamLog_log/`. `local_testing/of-test-data/` has small real rheoTool cases: `01-unDecomposed` (root time dirs), `02-decomposed` (4 `processorN/` dirs), `03-decomposed_collated_fh` (collated `processors4/`, an empty `processor0/`, and reconstructed root times). Run `foamTimes` against scratchpad copies of them, never the originals, and always try `--dry-run` before `--rm`.

## Architecture and conventions

```
src/pyfoamdecomp/
  case.py      shared case layout: time dirs, processor families, sides, union/completeness
  cli.py       fail(): "ERROR: ..." to stderr, exit 1 -- the single error path for every tool
  times.py     foamTimes
  logparse.py  foamLogParse
```

Shared logic lives in library modules (`case.py`; add more as tools need them) and each command is one module. Command modules follow the same layout:

1. Module docstring containing usage/examples -- it is reused as the argparse `epilog` (`times.py` slices it at `'Usage:'`).
2. Section banners (`# ----` comment blocks): helpers / parser -> actions / writers -> CLI.
3. `build_parser()` returns the `ArgumentParser` with `prog` set to the command name; `main(argv=None)` resolves paths, validates, dispatches; `if __name__ == '__main__': main()` so `python -m` works.
4. Errors go through `cli.fail()`.

Adding a tool: a new module in `src/pyfoamdecomp/`, a `[project.scripts]` entry (and the expected set in `tests/test_cli.py`), fixtures in `ofcase.py`, `tests/test_<module>.py`, and a README section. Move a helper into a shared module once a second tool needs it, not before.

### foamTimes (`times.py`, `case.py`)

- The timestep helpers in `case.py` take `sides`: a list of "sides", each a list of dirs holding time dirs -- `[case_dir]` (root) or the processor dirs of one family. `resolve_sides` picks them from `--root`/`--decomposed`: with neither, listing uses every side with times, while deleting errors if both sides have times, and never falls back to the root when processor dirs exist (pass `--root`). A root with only `0/` has no times unless `--with-zero`.
- Processor dirs are grouped into families (`find_processor_families`): classic `processorN` is one, collated `processorsN`/`processorsN_a-b` is one per N. Families without time dirs are ignored (collated runs often have an empty `processor0/`); more than one family with times is an error.
- Processor dirs matched by `PROCESSOR_DIR_RE`; bare `processors/` (old OF versions) is not supported.
- Timestep dirs matched by a numeric/scientific regex (`is_time_dir`), sorted by `float` value, not lexically.
- Timesteps are the **union** across all locations of the selected sides, so partially written (crashed) timesteps are still listed and deletable; deletion silently skips processors lacking that time.
- `--time` matches by numeric value (`find_time_by_value`) and acts on the real dir name in every selected side; no match is an error.
- `0/` is excluded unless `--with-zero`. `--dry-run` alone implies the delete path (prints only).
- `--keep-last N` counts only complete timesteps (`is_complete`: in every location of some selected side) toward N; partial timesteps newer than the oldest kept one are kept too, and nothing is deleted with N or fewer complete timesteps (`split_keep_last`).

### foamLogParse (`logparse.py`)

- `parse_log()` is a single-pass streaming state machine over the log; each `Time =` line appends a new record dict that later lines update in place. Key ordering facts it relies on:
  - `Courant Number ...` and `deltaT = ...` lines appear **before** `Time = T`, so they are buffered in `pending` and copied into the record when the `Time =` line arrives.
  - The last (possibly in-progress) step is already in `records`, which makes it safe on a live log.
  - Lines before the first `Time =` are ignored (the log header's `Time   : hh:mm:ss` uses a colon and does not match `^Time\s*=`).
- Solver fields are discovered dynamically from `Solving for <field>` and `Solving coupled for <field>` (rheoTool coupled p-U) lines (no solver-specific assumptions); coupled and segregated solves share the same per-field record. For fields solved multiple times per step (PISO/PIMPLE correctors): keep the **first** initial residual, the **last** final residual, and **sum** iterations.
- `RE_COURANT` uses `.search` (so `Interface Courant Number` matches); the other regexes use `.match` anchored at line start.
- Output: `TABLES` (courant/continuity/timing, written by `write_columns`) plus `write_residuals`; `#`-commented header, values formatted `{:.10e}` joined by two spaces, missing values as `NaN` -- intended for `np.loadtxt(..., comments='#')`. Keep this format stable when adding outputs.
- Logs are opened with `errors='replace'` to tolerate garbled interleaved MPI output.

Style: f-strings throughout `src/` (the tests still use `str.format`), `pathlib` for paths.

## Direction

`roadmap.txt` (gitignored, personal notes) lists planned tools: `foamFields`, `foamClean`, `foamPostProc` (postProcessing/ functionObject output with restart stitching), `foamFieldStats`, `foamReconstructHelper`, `foamCheckDecomp`, `foamDiskUsage`, `foamBoundaryInfo`, and others. Each becomes a module plus console entry point as described above, reusing `case.py` for processor-dir discovery and timestep scanning. New `--rm`-style destructive features should offer `--dry-run`, consistent with `foamTimes`.

When adding or changing a tool, update its section in `README.md` (usage block, options, examples).
