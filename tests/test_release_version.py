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


def test_release_build_tools_are_versioned_and_verified_before_execution():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "/continuous/" not in workflow
    assert "releases/download/${APPIMAGETOOL_VERSION}/" in workflow
    assert "releases/download/${APPIMAGE_RUNTIME_VERSION}/" in workflow
    assert "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0" in workflow
    assert "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d" in workflow
    assert workflow.count("sha256sum --check --strict") == 2
    assert workflow.index("sha256sum --check --strict") < workflow.index("chmod +x")
    assert '$RUNNER_TEMP/appimagetool-x86_64.AppImage' in workflow
    assert 'APPIMAGETOOL=$tool' in workflow
    assert 'APPIMAGE_RUNTIME_FILE=$runtime' in workflow


def test_only_upstream_release_workflow_marks_packages_official():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    spec = (ROOT / "MeetingRecorder.spec").read_text(encoding="utf-8")
    assert "github.repository == 'ludekvodicka/MeetingRecorder'" in workflow
    assert "MEETING_RECORDER_BUILD_CHANNEL" in workflow
    assert 'os.environ.get("MEETING_RECORDER_BUILD_CHANNEL") == "official"' in spec
    assert (ROOT / "packaging" / "official" / "build-channel.txt").read_text().strip() == "official"
