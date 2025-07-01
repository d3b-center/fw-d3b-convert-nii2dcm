"""Parser module to parse gear config.json."""

from flywheel_gear_toolkit import GearToolkitContext


# This function mainly parses gear_context's config.json file and returns relevant
# inputs and options.
def parse_config(
    gear_context: GearToolkitContext,
) -> bool:
    """Parse the config.json file.

    TODO: This may evolve with additional requirements.

    Returns:
        tuple: debug
    """

    debug = gear_context.config.get("debug")

    return debug