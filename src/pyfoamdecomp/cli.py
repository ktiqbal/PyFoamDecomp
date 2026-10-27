import sys


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)
