"""attachments.sh - the curl-config injection guard on the uploaded filename.

_common.sh's api() refuses a double quote or a newline in PATH because
either character breaks out of the `-K` curl config file's quoted value and
starts a second, attacker-chosen directive in the same file - one that still
inherits the `user = "email:TOKEN"` line already written above it.
attachments.sh builds its own config file the same way, for the multipart
upload api() cannot carry, and needs the identical guard on $FILE: uploading
a cloned repository's own images is this skill's documented job, so the
filename is exactly as attacker-controlled as an issue key or a page id.
"""

import os
import pathlib
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO / "skills" / "confluence-publish" / "scripts" / "attachments.sh"


class TestAttachmentsFilenameInjectionGuard(unittest.TestCase):
    def _run(self, *args):
        # HOME is overridden to a fresh, empty directory for every call so
        # this never sees - and can never accidentally use - a real
        # ~/.dbhq/atlassian/config.json on the machine running the suite.
        # require_config fails closed on a missing config file, so a test
        # that should get past the injection guard still stops before any
        # network call, deterministically, regardless of what is installed
        # on the host.
        isolated_home = tempfile.mkdtemp()
        env = {"HOME": isolated_home, "PATH": os.environ.get("PATH", "")}
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            capture_output=True, text=True, env=env,
        )

    def test_a_filename_containing_a_double_quote_is_refused(self):
        tmpdir = tempfile.mkdtemp()
        evil = pathlib.Path(tmpdir) / 'evil".txt'
        evil.write_text("data")
        result = self._run("upload", "1234567", str(evil))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Error:", result.stderr)
        self.assertIn("double quote", result.stderr)

    def test_a_filename_containing_a_newline_is_refused(self):
        tmpdir = tempfile.mkdtemp()
        evil = pathlib.Path(tmpdir) / "evil\nline.txt"
        evil.write_text("data")
        result = self._run("upload", "1234567", str(evil))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Error:", result.stderr)
        self.assertIn("newline", result.stderr)

    def test_an_ordinary_filename_is_not_rejected_by_this_guard(self):
        # Must fail - there is no credential in the isolated $HOME this test
        # runs with - but for THAT reason, not for looking like an
        # injection. require_config's own message is what should appear.
        tmpdir = tempfile.mkdtemp()
        ordinary = pathlib.Path(tmpdir) / "diagram.png"
        ordinary.write_text("data")
        result = self._run("upload", "1234567", str(ordinary))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("refusing to upload this file", result.stderr)
        self.assertIn("no credentials", result.stderr)


if __name__ == "__main__":
    unittest.main()
