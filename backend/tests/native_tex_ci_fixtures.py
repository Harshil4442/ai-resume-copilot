"""Explicit, fail-closed compiler prerequisite shared by real native tests."""

from backend.scripts.native_tex_test_image import required_test_image


def configure_native_compiler(monkeypatch) -> str:
    image = required_test_image()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("NATIVE_TEX_IMAGE", image)
    monkeypatch.delenv("NATIVE_TEX_COMPILER_URL", raising=False)
    return image
