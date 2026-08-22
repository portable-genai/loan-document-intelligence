"""Optional Google emulator detection for the ``local`` profile (opt-in, never required).

For the stores that have an official Google emulator, the local adapters can route to
it for higher-fidelity local development WHEN the standard emulator env var is set AND
the matching client library (from the ``[gcp]`` extra) imports. Otherwise the adapters
use their SDK-free SQLite / in-process path, which is the default.

This module only *detects* the opt-in; it deliberately performs **no google-cloud
import at module top level**. Each adapter that supports an emulator imports the google
client lazily, inside the method, and only on the emulator branch, so the default local
path and the offline test suite never import a google-cloud package.

There is no emulator for Document AI, Gemini, Model Armor or DLP, so those adapters stay
on the SDK-free workaround unconditionally.
"""

from __future__ import annotations

from ...envread import read_env_setting

# The three emulator-host reads below collapse UNSET and SET-AND-EMPTY onto the SAME answer,
# ``None``, and the collapse is deliberate: the emulator is an opt-in, so the closed direction
# is "no emulator, use the SDK-free path". An operator who empties the variable is turning the
# opt-in OFF, which is exactly where never having set it lands too. Contrast the platform base
# URLs, whose default is the pod's own loopback: there the two states must NOT collapse.

#: Standard emulator host env vars, by logical backend.
FIRESTORE_EMULATOR_ENV = "FIRESTORE_EMULATOR_HOST"
PUBSUB_EMULATOR_ENV = "PUBSUB_EMULATOR_HOST"
STORAGE_EMULATOR_ENV = "STORAGE_EMULATOR_HOST"


def firestore_emulator_host() -> str | None:
    """Return the Firestore emulator host if ``FIRESTORE_EMULATOR_HOST`` is set, else None."""
    return read_env_setting(FIRESTORE_EMULATOR_ENV).value or None


def pubsub_emulator_host() -> str | None:
    """Return the Pub/Sub emulator host if ``PUBSUB_EMULATOR_HOST`` is set, else None."""
    return read_env_setting(PUBSUB_EMULATOR_ENV).value or None


def storage_emulator_host() -> str | None:
    """Return the Cloud Storage emulator host if ``STORAGE_EMULATOR_HOST`` is set, else None."""
    return read_env_setting(STORAGE_EMULATOR_ENV).value or None


def firestore_client_available() -> bool:
    """Whether ``google-cloud-firestore`` is importable (the ``[gcp]`` extra is installed).

    The import is attempted lazily here (not at module top level) so that the default
    SDK-free local path never imports a google-cloud package.
    """
    try:
        import google.cloud.firestore  # noqa: F401  (lazy availability probe only)
    except Exception:  # noqa: BLE001 - any import failure means the emulator path is off
        return False
    return True


def firestore_emulator_active() -> bool:
    """True only when both the emulator env var is set AND the client lib imports."""
    return firestore_emulator_host() is not None and firestore_client_available()
