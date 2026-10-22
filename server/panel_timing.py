"""Numeric panel timings only; never record identities, paths or payloads."""
from contextlib import contextmanager
from time import perf_counter

PHASES = frozenset({'app', 'roots', 'catalog_wait', 'catalog_snapshot', 'catalog_roots',
                    'catalog_knowledge', 'catalog_scan', 'catalog_skills',
                    'catalog_profiles', 'catalog_validate'})


@contextmanager
def measure(timings, phase):
    started = perf_counter()
    try:
        yield
    finally:
        if timings is not None and phase in PHASES:
            timings[phase] = (perf_counter() - started) * 1000


def timing_header(timings):
    return ', '.join(f'{name};dur={value:.2f}' for name, value in timings.items()
                     if name in PHASES and type(value) in (int, float) and 0 <= value < float('inf'))
