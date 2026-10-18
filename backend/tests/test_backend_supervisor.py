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


def test_git_hook_never_signals_engine_or_installs_dependencies(tmp_path):
    root=tmp_path/"checkout"
    scripts=root/"scripts"
    scripts.mkdir(parents=True)
    source=Path(__file__).resolve().parents[2]/"scripts/auto_deploy.sh"
    target=scripts/source.name
    shutil.copy2(source,target)
    bins=tmp_path/"bin"
    bins.mkdir()
    git=bins/"git"
    git.write_text("#!/bin/bash\ncase \" $* \" in *rev-parse*) echo verified-test-sha;; *) echo backend/main.py;; esac\n")
    git.chmod(0o700)
    for command in ["pgrep","kill","pip","npm","systemctl"]:
        forbidden=bins/command
        forbidden.write_text("#!/bin/bash\necho unsafe >> "+str(tmp_path/"unsafe")+"\nexit 99\n")
        forbidden.chmod(0o700)
    result=subprocess.run(["bash",str(target)],env={**os.environ,"PATH":str(bins)+":"+os.environ["PATH"]},capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert not (tmp_path/"unsafe").exists()
    assert (root/".runtime/stack/backend-deploy-required").read_text().strip()=="verified-test-sha"
