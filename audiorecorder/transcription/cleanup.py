import os
import subprocess
import sys

from PyQt6.QtCore import QObject, pyqtSignal


def _get_startupinfo():
    if sys.platform != "win32":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si


class CleanupWorker(QObject):
    progress = pyqtSignal(str)
    completed = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, transcript_path, prompt_template):
        super().__init__()
        self.transcript_path = transcript_path
        self.prompt_template = prompt_template

    def run(self):
        try:
            transcript_dir = os.path.dirname(self.transcript_path)
            transcript_name = os.path.basename(self.transcript_path)
            summary_name = os.path.splitext(transcript_name)[0] + ".summary.md"
            summary_path = os.path.join(transcript_dir, summary_name)

            # Let Claude work agentically: it reads the transcript and writes the
            # summary itself, running with the transcript directory as cwd.
            prompt = self.prompt_template.format(
                transcript_name=transcript_name, summary_name=summary_name,
            )

            self.progress.emit("Running Claude cleanup...")
            result = subprocess.run(
                ["claude", "-p", prompt,
                 "--model", "sonnet",
                 "--dangerously-skip-permissions"],
                cwd=transcript_dir,
                stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=600,
                encoding="utf-8",
                startupinfo=_get_startupinfo(),
            )

            if result.returncode != 0:
                stderr = result.stderr.strip() if result.stderr else "unknown error"
                raise RuntimeError(f"Claude CLI failed (code {result.returncode}): {stderr}")

            if not os.path.exists(summary_path):
                out = (result.stdout or "").strip()
                raise RuntimeError(
                    "Summary file was not created by Claude.\n"
                    f"Claude output: {out[:500] or '(empty)'}"
                )

            self.progress.emit("Done")
            self.completed.emit(summary_path)

        except subprocess.TimeoutExpired:
            self.error.emit("Claude CLI timed out after 10 minutes")
        except FileNotFoundError:
            self.error.emit("Claude CLI not found. Make sure 'claude' is in PATH.")
        except Exception as e:
            self.error.emit(str(e))
