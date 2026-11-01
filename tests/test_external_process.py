import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from im_bridge.core.external_process import clean_environment, run_external


class ExternalRuntimeTests(unittest.TestCase):
    def test_removes_parent_python_paths_and_frozen_loader_vars(self):
        with patch.dict(os.environ, {"PYTHONPATH":"foreign-python", "PYTHONHOME":"foreign-home",
                                     "_PYI_APPLICATION_HOME_DIR":"qt-runtime"}):
            result=clean_environment()
        self.assertNotIn("PYTHONPATH",result)
        self.assertNotIn("PYTHONHOME",result)
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR",result)
        self.assertEqual(result["PYTHONUTF8"],"1")

    def test_non_utf8_child_logs_cannot_break_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            script=Path(tmp)/"child.py"
            script.write_text("import sys,os,json; sys.stderr.buffer.write(b'\\xff'); print(json.dumps({'path':os.getenv('PYTHONPATH')}))",encoding="utf-8")
            with patch.dict(os.environ,{"PYTHONPATH":"foreign-python"}):
                result=run_external([sys.executable,str(script)],cwd=tmp,timeout=10)
            self.assertEqual(result.returncode,0)
            self.assertIsNone(json.loads(result.stdout)["path"])
            self.assertIn("\ufffd",result.stderr)
