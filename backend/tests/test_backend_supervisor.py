"""Exercise the real shell supervisor using an isolated worker and logs."""
import os
from pathlib import Path
import shutil
import subprocess
import time


def test_child_exit_during_grace_restarts_promptly(tmp_path):
    source=Path(__file__).resolve().parents[1]/"sterling_supervisor.sh"
    shutil.copy2(source,tmp_path/source.name)
    worker=tmp_path/"run_backend.sh"
    worker.write_text("#!/bin/bash\necho started >> starts\nexit 23\n")
    worker.chmod(0o700)
    env={**os.environ,"STERLING_STARTUP_GRACE":"120"}
    process=subprocess.Popen(["bash",str(tmp_path/source.name)],env=env,
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            starts=tmp_path/"starts"
            if starts.exists() and len(starts.read_text().splitlines())>=2:
                break
            time.sleep(.1)
        else:
            raise AssertionError("dead worker was not restarted during grace")
        assert "status=23" in (tmp_path/"logs/supervisor.log").read_text()
    finally:
        process.terminate()
        process.wait(timeout=8)
