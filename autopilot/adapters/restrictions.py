"""Known out-of-contract names. Not semantic analysis or an executor sandbox.

An unrecognized name is not proof of read-only behavior; the user must
consciously select each executor through the future authenticated panel.
"""
import re

_EXCLUDED = frozenset({'publish', 'deploy', 'production', 'payment', 'payments',
                       'purchase', 'delete', 'remove', 'unlink', 'rmtree', 'secret',
                       'secrets', 'shell', 'install', 'pytest', 'unittest'})


def restricted_name(*names):
    for name in names:
        words = re.sub(r'([a-z])([A-Z])', r'\1_\2', name).casefold()
        if set(re.split('[^a-z0-9]+', words)) & _EXCLUDED or words in {'execute_command', 'run_command', 'run_tests'}:
            return True
    return False
