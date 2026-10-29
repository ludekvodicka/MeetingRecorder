import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
EXTENSION = ROOT / "browser-extension"


def manifest():
    return json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_uses_the_current_extension_format():
    assert manifest()["manifest_version"] == 3


def test_it_has_no_browser_history_tab_or_capture_permission():
    permissions = set(manifest().get("permissions", []))
    assert permissions.isdisjoint({"tabs", "history", "tabCapture", "desktopCapture"})


def test_localhost_is_the_only_background_host_permission():
    assert manifest()["host_permissions"] == ["http://127.0.0.1:8765/*"]


def test_content_access_is_limited_to_supported_meeting_services():
    matches = set(manifest()["content_scripts"][0]["matches"])
    assert matches == {
        "https://meet.google.com/*",
        "https://*.zoom.us/wc/*",
        "https://teams.microsoft.com/*",
        "https://teams.cloud.microsoft/*",
    }
    assert "<all_urls>" not in matches


def test_every_declared_script_is_packaged():
    data = manifest()
    scripts = [data["background"]["service_worker"]]
    scripts.extend(data["content_scripts"][0]["js"])
    assert all((EXTENSION / script).is_file() for script in scripts)


def test_the_extension_documents_local_only_behavior():
    readme = (EXTENSION / "README.md").read_text(encoding="utf-8")
    assert "127.0.0.1" in readme
    assert "does not read browser history" in readme


def test_javascript_parses_when_node_is_available():
    node = shutil.which("node")
    if node is None:
        return
    for script in ("background.js", "content.js"):
        subprocess.run(
            [node, "--check", str(EXTENSION / script)],
            check=True,
            capture_output=True,
            text=True,
        )


def test_background_reports_presence_without_an_active_meeting():
    script = (EXTENSION / "background.js").read_text(encoding="utf-8")
    assert 'type: "companion-presence"' in script
    assert "reportPresence();" in script
