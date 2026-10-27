"""
OpenFOAM case layout: time directories and processor directories.

A "side" is a list of directories holding time directories: [case_dir] for
the root, or the processor dirs of one family. Helpers take a list of sides.
"""

import re

PROCESSOR_DIR_RE = r'(processors?)([0-9]+)(_[0-9]+-[0-9]+)?'


def is_time_dir(name):
    """Return True if the directory name looks like an OpenFOAM timestep (0, 1.5, 1e-3, ...)."""
    return bool(re.fullmatch(r'[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?', name))


def processor_dir_key(path):
    """Numeric sort key, so processor2 sorts before processor10 and processors4_2-3 after _0-1."""
    _, n, ranks = re.fullmatch(PROCESSOR_DIR_RE, path.name).groups()
    return int(n), int(ranks[1:].split('-')[0]) if ranks else -1


def find_processor_families(case_dir):
    """
    Group processor dirs by layout: classic processorN dirs form one family,
    collated processorsN and processorsN_a-b dirs form one family per N.
    """
    families = {}
    for p in case_dir.iterdir():
        m = re.fullmatch(PROCESSOR_DIR_RE, p.name)
        if m and p.is_dir():
            key = 'processor' if m.group(1) == 'processor' else 'processors' + m.group(2)
            families.setdefault(key, []).append(p)
    return {k: sorted(dirs, key=processor_dir_key) for k, dirs in families.items()}


def side_label(side):
    """'root', a single processor dir's name, or a family glob like 'processor*'."""
    m = re.fullmatch(PROCESSOR_DIR_RE, side[0].name)
    if m is None:
        return 'root'
    if len(side) == 1:
        return side[0].name
    return 'processor*' if m.group(1) == 'processor' else f'processors{m.group(2)}_*'


def all_locations(sides):
    return [loc for side in sides for loc in side]


def get_times_union(sides, include_zero=False):
    """
    Return sorted list of all timestep names present in ANY location
    (i.e. the union), optionally including '0'.
    """
    times = set()
    for loc in all_locations(sides):
        for entry in loc.iterdir():
            if entry.is_dir() and is_time_dir(entry.name):
                if not include_zero and entry.name == '0':
                    continue
                times.add(entry.name)
    return sorted(times, key=float)


def find_time_by_value(value, sides):
    """Return the timestep names whose numeric value equals value (e.g. '8.0' -> ['8'])."""
    return [t for t in get_times_union(sides, include_zero=True) if float(t) == float(value)]


def is_complete(t, sides):
    """True if some side has t in every one of its locations."""
    return any(all((loc / t).is_dir() for loc in side) for side in sides)
