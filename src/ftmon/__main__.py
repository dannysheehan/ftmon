"""`python -m ftmon` — same entry as the `ftmon` console script. Exists so
the e2e harness (TS-05) can spawn the daemon with sys.executable and no
dependence on installed script shims."""

import sys

from ftmon.cli import main

sys.exit(main())
