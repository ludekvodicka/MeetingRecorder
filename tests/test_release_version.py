import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_check_includes_the_extension_version():
    check = load_script("check_release_version")
    assert check.check("", "") is None


def test_set_version_synchronizes_the_extension(tmp_path, monkeypatch):
    setter = load_script("set_version")
    version_file = tmp_path / "version.py"
    version_file.write_text('__version__ = "0.2.0"\n', encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"version": "0.2.0"}\n', encoding="utf-8")
    monkeypatch.setattr(setter, "VERSION_FILE", version_file)
    monkeypatch.setattr(setter, "EXTENSION_MANIFEST", manifest)

    setter.set_version("0.3.0")

    assert '__version__ = "0.3.0"' in version_file.read_text(encoding="utf-8")
    assert json.loads(manifest.read_text(encoding="utf-8"))["version"] == "0.3.0"
