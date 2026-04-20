"""Smoke: the package imports and exposes its public API."""

import family_office_base as fob


def test_public_api_is_importable() -> None:
    # Touch every name in the public API so regressions in __all__ fail here
    # rather than when a leaf skill tries to import.
    for name in fob.__all__:
        assert hasattr(fob, name), f"missing public export: {name}"


def test_version_string() -> None:
    assert isinstance(fob.__version__, str)
    assert fob.__version__.count(".") == 2
