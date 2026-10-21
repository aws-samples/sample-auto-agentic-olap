"""Run `dbt parse` in environments where ProcessPoolExecutor is unavailable.

MetricFlow's semantic manifest validator constructs a ProcessPoolExecutor, which
raises PermissionError on sandboxes that block `sysconf("SC_SEM_NSEMS_MAX")`.
The executor is never used for the default synchronous validation path, so
reporting a plausible limit is enough to let parsing proceed.
"""

import os
import sys

_real_sysconf = os.sysconf


def _sysconf(name):  # type: ignore[no-untyped-def]
    if name == "SC_SEM_NSEMS_MAX":
        return 256
    return _real_sysconf(name)


os.sysconf = _sysconf  # type: ignore[assignment]

from dbt.cli.main import dbtRunner  # noqa: E402


def main() -> int:
    args = sys.argv[1:] or ["parse", "--no-partial-parse"]
    result = dbtRunner().invoke(args)
    if result.exception is not None:
        raise result.exception
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
