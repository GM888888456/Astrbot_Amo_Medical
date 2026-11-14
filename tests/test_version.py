# -*- coding: utf-8 -*-
"""发版自检：metadata.yaml / CHANGELOG.md / README.md / main.py 四处版本号必须一致。

医疗仓库以前没有这条用例，README 落后两个版本也没人发现。
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_check_version_passes():
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "check_version.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
