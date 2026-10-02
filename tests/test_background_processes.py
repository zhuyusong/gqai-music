"""Exercise media children from the same console-free parent as the desktop app."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt", "Windows console behavior")
class BackgroundProcessTests(unittest.TestCase):
    def test_probe_from_pythonw_preserves_output_and_errors_without_a_console(self):
        worker = r'''
import ctypes, json, subprocess, sys
from pathlib import Path
from unittest.mock import patch
from gqai_music import media

report = {"parent_console": ctypes.windll.kernel32.GetConsoleWindow()}
original_run = subprocess.run
child = "import ctypes,json,sys; print(json.dumps({'format':{'duration':'1'},'streams':[]})); print(ctypes.windll.kernel32.GetConsoleWindow(), file=sys.stderr)"
def launch(_command, **kwargs):
    result = original_run([str(Path(sys.executable).with_name('python.exe')), '-c', child], **kwargs)
    report['child_console'] = int(result.stderr.strip())
    return result

with patch.object(media.subprocess, 'run', side_effect=launch):
    report['media'] = media.probe(Path(sys.argv[1]))
    child = "import ctypes,sys; print(ctypes.windll.kernel32.GetConsoleWindow(), file=sys.stderr); sys.exit(7)"
    try:
        media.probe(Path(sys.argv[1]))
    except subprocess.CalledProcessError as error:
        report['failure'] = {'code': error.returncode, 'console': int(error.stderr.strip())}
Path(sys.argv[2]).write_text(json.dumps(report), encoding='utf-8')
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"test")
            report = Path(directory) / "report.json"
            completed = subprocess.run(
                [str(Path(sys.executable).with_name("pythonw.exe")), "-c", worker,
                 str(source), str(report)],
                capture_output=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            value = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(value["parent_console"], 0)
            self.assertEqual(value["child_console"], 0)
            self.assertEqual(value["media"]["duration_seconds"], 1)
            self.assertEqual(value["failure"], {"code": 7, "console": 0})

