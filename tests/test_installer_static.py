"""The installer and backup script, checked without a container: build it, read what it ships, run the backup."""
import glob
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKER = "__PAYLOAD_BELOW__"


def build(cwd=ROOT, env=None):
    return subprocess.run(["sh", "installer/build.sh"], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def payload_names(installer):
    """File names in the tarball that the installer unpacks, read the way the installer reads it."""
    script = f"sed -n '/^{MARKER}$/,$p' \"$1\" | tail -n +2 | base64 -d | tar -tzf -"
    out = subprocess.run(["sh", "-c", script, "sh", installer], check=True, capture_output=True, text=True).stdout
    return {line.rstrip("/") for line in out.splitlines()}


def mini_repo(tmp_path, static=False, deploy_env=None):
    """A copy of the build inputs with stand-in modules, so build.sh can be tried with and without static/."""
    for name in ("installer/build.sh", "installer/setup-head.sh", "ops/tracker-backup"):
        os.makedirs(tmp_path / os.path.dirname(name), exist_ok=True)
        shutil.copy(os.path.join(ROOT, name), tmp_path / name)
    for name in ("app.py", "zzz_later_module.py"):
        (tmp_path / name).write_text("x = 1\n")
    if static:
        os.makedirs(tmp_path / "static" / "js")
        (tmp_path / "static" / "index.html").write_text("<p>hi</p>")
        (tmp_path / "static" / "js" / "a.js").write_text("export {};\n")
    if deploy_env is not None:
        os.makedirs(tmp_path / "deploy", exist_ok=True)
        (tmp_path / "deploy" / "deploy.env").write_text(deploy_env)
    return str(tmp_path)


def test_build_produces_a_syntactically_valid_installer():
    build()
    subprocess.run(["sh", "-n", "dist/setup-tracker.sh"], cwd=ROOT, check=True)


def test_installer_binds_loopback_and_never_sets_dev_email():
    head = open(os.path.join(ROOT, "installer", "setup-head.sh")).read()
    assert "127.0.0.1:" in head and "0.0.0.0" not in head and "DEV_EMAIL" not in head
    built = open(os.path.join(ROOT, "dist", "setup-tracker.sh")).read() if build() else ""
    assert "0.0.0.0" not in built and "DEV_EMAIL" not in built


def test_every_python_module_is_in_the_built_payload():
    build()
    shipped = payload_names(os.path.join(ROOT, "dist", "setup-tracker.sh"))
    modules = {os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "*.py"))}
    assert modules, "no modules found"
    missing = {f"tracker/{m}" for m in modules} - shipped
    assert not missing, f"not shipped: {sorted(missing)}"
    assert "tracker/ops/tracker-backup" in shipped
    assert not any("__pycache__" in n or n.startswith("tracker/tests") for n in shipped)


def test_build_ships_new_modules_by_glob_and_static_only_when_present(tmp_path):
    repo = mini_repo(tmp_path)
    build(repo)  # no static/: must not fail
    shipped = payload_names(os.path.join(repo, "dist", "setup-tracker.sh"))
    assert {"tracker/app.py", "tracker/zzz_later_module.py", "tracker/ops/tracker-backup"} <= shipped
    assert not any(n.startswith("tracker/static") for n in shipped)

    os.makedirs(tmp_path / "with_static")
    repo = mini_repo(tmp_path / "with_static", static=True)
    build(repo)
    shipped = payload_names(os.path.join(repo, "dist", "setup-tracker.sh"))
    assert {"tracker/static/index.html", "tracker/static/js/a.js", "tracker/zzz_later_module.py"} <= shipped


def test_every_setting_is_in_env_example_and_the_installer():
    src = open(os.path.join(ROOT, "settings.py")).read()
    names = set(re.findall(r'os\.environ\.get\("([A-Z_]+)"', src)) | set(re.findall(r'_int\("([A-Z_]+)"', src))
    names -= {"DEV_EMAIL", "DATA_DIR"}
    assert {"TRACKER_PORT", "ALLOWED_EMAILS", "USERS", "CF_TEAM_DOMAIN", "CF_AUD", "APP_NAME", "LOCK_IDLE_SECONDS"} <= names
    env_example = open(os.path.join(ROOT, ".env.example")).read()
    head = open(os.path.join(ROOT, "installer", "setup-head.sh")).read()
    installer_settings = set(re.findall(r'^add_setting "\$CONF" ([A-Z_]+) ', head, re.M))
    deploy_example = open(os.path.join(ROOT, "deploy", "deploy.env.example")).read()
    for name in names:
        assert re.search(rf"^{name}=", env_example, re.M), f"{name} missing from .env.example"
        assert name in installer_settings, f"{name} missing from the installer's settings list"
    assert installer_settings <= names, "installer writes a setting settings.py does not read"
    assert "DEV_EMAIL" not in env_example and "DEV_EMAIL" not in deploy_example


def test_deploy_env_defaults_are_baked_in_and_the_environment_wins(tmp_path):
    repo = mini_repo(tmp_path, deploy_env='ALLOWED_EMAILS=alex@example.com,sam@example.com\nAPP_NAME="Ledger"\nCF_AUD=\n')
    build(repo)
    out = open(os.path.join(repo, "dist", "setup-tracker.sh")).read()
    assert ': "${ALLOWED_EMAILS:=alex@example.com,sam@example.com}"' in out
    assert ': "${APP_NAME:=Ledger}"' in out
    assert "CF_AUD:=" not in out  # empty values are not baked in
    subprocess.run(["sh", "-n", os.path.join(repo, "dist", "setup-tracker.sh")], check=True)


@pytest.mark.parametrize("bad", ['a"b', "a$b", "a`b", "a\\b"])
def test_deploy_env_rejects_characters_that_would_break_the_installer(tmp_path, bad):
    repo = mini_repo(tmp_path, deploy_env=f"USERS={bad}\n")
    r = subprocess.run(["sh", "installer/build.sh"], cwd=repo, capture_output=True, text=True)
    assert r.returncode != 0 and "USERS" in r.stdout


@pytest.mark.skipif(not shutil.which("sqlite3"), reason="sqlite3 CLI not installed")
def test_backup_is_private_valid_and_prunes_old_copies(tmp_path):
    data, backups = tmp_path / "data", tmp_path / "backups"
    data.mkdir()
    con = sqlite3.connect(data / "tracker.db")
    con.execute("create table t (v text)"); con.execute("insert into t values ('secret')"); con.commit(); con.close()
    backups.mkdir(mode=0o755)
    old = backups / "tracker-2000-01-01.db"
    old.write_text("old")
    ancient = time.time() - 30 * 86400
    os.utime(old, (ancient, ancient))
    env = {**os.environ, "DATA_DIR": str(data), "BACKUP_DIR": str(backups), "LOG": str(tmp_path / "log")}
    subprocess.run(["sh", os.path.join(ROOT, "ops", "tracker-backup")], check=True, env=env)
    copies = list(backups.glob("tracker-*.db"))
    assert [c.name for c in copies] == [f"tracker-{time.strftime('%Y-%m-%d')}.db"], "old copy not pruned or new one missing"
    assert stat.S_IMODE(copies[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(backups.stat().st_mode) == 0o700
    assert sqlite3.connect(copies[0]).execute("select v from t").fetchone() == ("secret",)
    assert not list(backups.glob("*.part"))


@pytest.mark.skipif(not shutil.which("sqlite3"), reason="sqlite3 CLI not installed")
def test_backup_with_no_database_is_not_an_error(tmp_path):
    env = {**os.environ, "DATA_DIR": str(tmp_path / "none"), "BACKUP_DIR": str(tmp_path / "b"), "LOG": str(tmp_path / "log")}
    subprocess.run(["sh", os.path.join(ROOT, "ops", "tracker-backup")], check=True, env=env)
