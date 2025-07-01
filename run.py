#!/usr/bin/env python
"""The run script"""

import logging
import sys

from flywheel_gear_toolkit import GearToolkitContext

from fw_gear_convert_nii2dcm.main import run
from fw_gear_convert_nii2dcm.parser import parse_config

log = logging.getLogger(__name__)


def main(context: GearToolkitContext) -> None:  # pragma: no cover
    """Parses config and run"""

    debug = parse_config(context)

    e_code = run(context, debug)

    # Exit the python script (and thus the container) with the exit code
    sys.exit(e_code)


if __name__ == "__main__":  # pragma: no cover
    # Get access to gear config, inputs, and sdk client if enabled.
    with GearToolkitContext() as gear_context:
        # Initialize logging
        gear_context.init_logging()

        # Pass the gear context into main function defined above.
        main(gear_context)
