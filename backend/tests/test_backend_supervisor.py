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


def test_running_supervisor_survives_in_place_script_update(tmp_path):
    source=Path(__file__).resolve().parents[1]/"sterling_supervisor.sh"
    target=tmp_path/source.name
    shutil.copy2(source,target)
    worker=tmp_path/"run_backend.sh"
    worker.write_text("#!/bin/bash\nexec sleep 120\n")
    worker.chmod(0o700)
    env={**os.environ,"STERLING_STARTUP_GRACE":"1", "STERLING_PROBE_INTERVAL":"1",
         "STERLING_PROBE_URL":"http://127.0.0.1:1", "STERLING_PROBE_MAX_FAILURES":"100"}
    process=subprocess.Popen(["bash",str(target)],env=env,
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+5
        while not (tmp_path/"logs/supervisor.log").exists():
            assert time.monotonic()<deadline
            time.sleep(.05)
        target.write_text("this intentionally invalid replacement is a different length\n")
        time.sleep(3)
        assert process.poll() is None
        assert "probe FAILED" in (tmp_path/"logs/supervisor.log").read_text()
    finally:
        process.terminate()
        process.wait(timeout=8)
