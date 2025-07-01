"""The fw_gear_fhir_bundle_exporter package."""
from importlib.metadata import version

try:
    __version__ = version(__package__)
except Exception:  # pragma: no cover
    pass
