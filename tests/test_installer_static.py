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


# ---------------------------------------------------------------- instance names (a second install on one container)
HEAD = os.path.join(ROOT, "installer", "setup-head.sh")
NAME_VARS = ("NAME", "APP_DIR", "DATA_DIR", "BACKUP_DIR", "CONF", "INITD", "LOG", "SVC_USER", "BACKUP_JOB", "DEFAULT_PORT", "LABEL")

DEFAULT_VALUES = {
    "NAME": "tracker", "APP_DIR": "/opt/tracker", "DATA_DIR": "/var/lib/tracker", "BACKUP_DIR": "/var/backups/tracker",
    "CONF": "/etc/conf.d/tracker", "INITD": "/etc/init.d/tracker", "LOG": "/var/log/tracker.log", "SVC_USER": "tracker",
    "BACKUP_JOB": "/etc/periodic/daily/tracker-backup", "DEFAULT_PORT": "8767", "LABEL": "",
}

# The service script of a default install: what the installer wrote before instance names existed, plus MODE,
# WISHLIST_URL and WISHLIST_TOKEN in the exported names (new settings). Any other difference is a change to the
# default install.
DEFAULT_INIT = """#!/sbin/openrc-run
name="tracker"
description="Gift Tracker (Flask + gunicorn, loopback only)"
supervisor="supervise-daemon"
respawn_delay=5
respawn_max=10
respawn_period=600
command="/usr/bin/gunicorn"
command_user="tracker"
output_log="/var/log/tracker.log"
error_log="/var/log/tracker.log"

depend() { need net; after firewall; }

start_pre() {
  : ${TRACKER_PORT:=8767}
  command_args="--preload --workers 2 --threads 4 --timeout 60 --bind 127.0.0.1:${TRACKER_PORT} --chdir /opt/tracker --error-logfile /var/log/tracker.log app:app"
  export HOME=/var/lib/tracker DATA_DIR=/var/lib/tracker
  export TRACKER_PORT ALLOWED_EMAILS USERS CF_TEAM_DOMAIN CF_AUD APP_NAME LOCK_IDLE_SECONDS MODE WISHLIST_URL WISHLIST_TOKEN
  checkpath -f -o tracker:tracker -m 0640 /var/log/tracker.log
}
"""  # noqa: E501  (the golden text has the installer's long command_args line)


def fragment(begin, end):
    """The part of the installer between `# <begin>` and `# <end>` marker lines: the tests run exactly that."""
    m = re.search(rf"^# {begin}\n(.*?)^# {end}\n", open(HEAD).read(), re.S | re.M)
    assert m, f"markers {begin}/{end} not found in installer/setup-head.sh"
    return m.group(1)


def run_fragments(args=(), instance_env=None, initd=None):
    """Run the instance fragment (and, with a path, the init-script fragment writing there) the way the installer
    does, with `args` as its arguments. Returns (exit status, output, variables, init script text or None)."""
    env = {k: v for k, v in os.environ.items() if k != "TRACKER_INSTANCE"}
    env.update(instance_env or {})
    script = "set -eu\n" + fragment("instance-begin", "instance-end")
    script += "".join(f'printf "%s=%s\\n" {v} "${{{v}}}"\n' for v in NAME_VARS)
    if initd:
        script += f'INITD="{initd}"\n' + fragment("initd-begin", "initd-end")
    r = subprocess.run(["sh", "-c", script, "sh", *args], env=env, capture_output=True, text=True)
    values = dict(line.split("=", 1) for line in r.stdout.splitlines() if line.split("=", 1)[0] in NAME_VARS)
    text = open(initd).read() if initd and r.returncode == 0 else None
    return r.returncode, r.stdout + r.stderr, values, text


def test_default_install_names_paths_and_port_are_unchanged():
    status, _, values, _ = run_fragments()
    assert status == 0 and values == DEFAULT_VALUES


def test_default_service_script_is_what_it_was(tmp_path):
    status, _, _, text = run_fragments(initd=str(tmp_path / "tracker"))
    assert status == 0 and text == DEFAULT_INIT


def test_uninstall_alone_still_means_the_default_install():
    status, _, values, _ = run_fragments(["--uninstall"])
    assert status == 0 and values == DEFAULT_VALUES


def test_an_instance_gets_its_own_names_paths_user_and_port(tmp_path):
    status, _, values, text = run_fragments(["--instance", "school"], initd=str(tmp_path / "tracker-school"))
    assert status == 0
    assert values == {
        "NAME": "tracker-school", "APP_DIR": "/opt/tracker-school", "DATA_DIR": "/var/lib/tracker-school",
        "BACKUP_DIR": "/var/backups/tracker-school", "CONF": "/etc/conf.d/tracker-school", "INITD": "/etc/init.d/tracker-school",
        "LOG": "/var/log/tracker-school.log", "SVC_USER": "tracker-school", "BACKUP_JOB": "/etc/periodic/daily/tracker-school-backup",
        "DEFAULT_PORT": "8768", "LABEL": " (tracker-school)",
    }
    for key, default in DEFAULT_VALUES.items():                   # nothing is shared with the default install
        if key not in ("LABEL", "DEFAULT_PORT"):
            assert values[key] != default, key
    assert 'name="tracker-school"' in text and 'command_user="tracker-school"' in text
    assert "--chdir /opt/tracker-school --error-logfile /var/log/tracker-school.log" in text
    assert "export HOME=/var/lib/tracker-school DATA_DIR=/var/lib/tracker-school" in text
    assert ": ${TRACKER_PORT:=8768}" in text and "checkpath -f -o tracker-school:tracker-school -m 0640 /var/log/tracker-school.log" in text
    assert "127.0.0.1:${TRACKER_PORT}" in text
    # the only lines that differ from a default install are the ones that name it
    assert [line for line in text.splitlines() if line not in DEFAULT_INIT.splitlines()] and "/opt/tracker " not in text
    assert not re.search(r"/(opt|var/lib|var/log)/tracker\b(?!-)", text) and 'name="tracker"' not in text


@pytest.mark.parametrize("how", [["--instance", "school"], ["--instance=school"]])
def test_the_flag_and_the_environment_name_the_same_instance(how):
    flag = run_fragments(how)[2]
    assert flag["NAME"] == "tracker-school"
    assert run_fragments(instance_env={"TRACKER_INSTANCE": "school"})[2] == flag
    assert run_fragments(["--uninstall", *how])[2] == flag
    assert run_fragments([*how, "--uninstall"])[2] == flag


def test_the_flag_wins_over_the_environment():
    assert run_fragments(["--instance", "b"], instance_env={"TRACKER_INSTANCE": "a"})[2]["NAME"] == "tracker-b"


def test_a_generic_environment_variable_does_not_rename_the_install():
    """INSTANCE and NAME are common names in other tooling; only TRACKER_INSTANCE counts."""
    assert run_fragments(instance_env={"INSTANCE": "i-0abc", "NAME": "box"})[2] == DEFAULT_VALUES


@pytest.mark.parametrize("bad", ["School", "a b", "-x", "a/b", "a.b", "a_b", "x" * 17, "../x", "a;b", "$(id)", "ü"])
def test_a_bad_instance_name_is_refused(bad):
    status, out, values, _ = run_fragments(["--instance", bad])
    assert status == 1 and values == {} and "instance name" in out


@pytest.mark.parametrize("args", [["--instnace", "school"], ["school"], ["--instance"], ["--instance="], ["--uninstal"]])
def test_a_mistyped_argument_is_refused_instead_of_touching_the_default_install(args):
    status, out, values, _ = run_fragments(args)
    assert status == 1 and values == {}, out


def test_a_name_of_sixteen_characters_is_accepted():
    assert run_fragments(["--instance", "a" * 16])[2]["NAME"] == "tracker-" + "a" * 16


def test_the_installer_only_acts_on_names_from_the_instance_block():
    """No path, service or user of the default install is left hard-coded outside the template and the block."""
    text = open(HEAD).read()
    outside = text.replace(fragment("instance-begin", "instance-end"), "").replace(fragment("initd-begin", "initd-end"), "")
    outside = "\n".join(line for line in outside.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"/(opt|var/lib|var/backups|var/log|etc/conf\.d|etc/init\.d)/tracker\b", outside.replace("/opt/tracker.new", "")), outside
    assert "rc-service tracker" not in outside and "rc-update add tracker" not in outside


def test_a_named_instances_daily_backup_runs_the_script_on_its_own_folders():
    text = open(HEAD).read()
    assert 'printf \'DATA_DIR=%s; BACKUP_DIR=%s; LOG=/var/log/%s-backup.log\\n\' "$DATA_DIR" "$BACKUP_DIR" "$NAME"' in text
    assert "export DATA_DIR BACKUP_DIR LOG" in text and 'exec %s/ops/tracker-backup' in text
    assert 'install -m 755 "$APP_DIR/ops/tracker-backup" "$BACKUP_JOB"' in text    # the default install's job is the script itself


def test_the_built_installer_has_the_instance_support_and_still_parses():
    build()
    built = open(os.path.join(ROOT, "dist", "setup-tracker.sh")).read()
    assert "# instance-begin" in built and "--instance" in built
    subprocess.run(["sh", "-n", "dist/setup-tracker.sh"], cwd=ROOT, check=True)
    assert "DEV_EMAIL" not in built and "0.0.0.0" not in built


def test_mode_is_baked_from_the_env_file_and_the_environment_wins(tmp_path):
    repo = mini_repo(tmp_path, deploy_env="MODE=school\nAPP_NAME=Ledger\n")
    build(repo)
    out = open(os.path.join(repo, "dist", "setup-tracker.sh")).read()
    assert ': "${MODE:=school}"' in out
    subprocess.run(["sh", "-n", os.path.join(repo, "dist", "setup-tracker.sh")], check=True)


# ---------------------------------------------------------------- the MCP service (docs/mcp-setup.md)
MCP_VARS = ("MCP_NAME", "MCP_DIR", "MCP_VENV", "MCP_CONF", "MCP_INITD", "MCP_LOG", "MCP_DEFAULT_PORT")
DEFAULT_MCP_INIT = """#!/sbin/openrc-run
name="tracker-mcp"
description="Gift Tracker MCP server (Streamable HTTP, behind Cloudflare Access)"
supervisor="supervise-daemon"
respawn_delay=10
respawn_max=10
respawn_period=600
command="/opt/tracker-mcp/venv/bin/python"
command_args="/opt/tracker/mcp_http.py"
command_user="tracker-mcp:tracker-mcp"
output_log="/var/log/tracker-mcp.log"
error_log="/var/log/tracker-mcp.log"

depend() { need net tracker; after firewall; }

start_pre() {
  export TRACKER_API_TOKEN TRACKER_URL MCP_HTTP_BIND MCP_HTTP_PORT MCP_HTTP_PATH MCP_PUBLIC_HOSTS CF_ACCESS_TEAM_DOMAIN CF_ACCESS_AUD MCP_ALLOWED_EMAILS
  checkpath -f -o tracker-mcp:tracker-mcp -m 0640 /var/log/tracker-mcp.log
}
"""  # noqa: E501
MCP_SETTINGS = {"TRACKER_API_TOKEN", "TRACKER_URL", "MCP_HTTP_BIND", "MCP_HTTP_PORT", "MCP_HTTP_PATH", "MCP_PUBLIC_HOSTS",
                "CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "MCP_ALLOWED_EMAILS"}


def run_mcp_fragments(args=(), initd=None):
    """The instance block, then (with a path) the MCP service template writing there. Returns (values, script text)."""
    env = {k: v for k, v in os.environ.items() if k != "TRACKER_INSTANCE"}
    script = "set -eu\n" + fragment("instance-begin", "instance-end")
    script += "".join(f'printf "%s=%s\\n" {v} "${{{v}}}"\n' for v in MCP_VARS)
    if initd:
        script += f'MCP_INITD="{initd}"\n' + fragment("mcp-initd-begin", "mcp-initd-end")
    r = subprocess.run(["sh", "-c", script, "sh", *args], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    values = dict(line.split("=", 1) for line in r.stdout.splitlines() if line.split("=", 1)[0] in MCP_VARS)
    return values, (open(initd).read() if initd else None)


def test_the_mcp_service_of_the_default_install(tmp_path):
    values, text = run_mcp_fragments(initd=str(tmp_path / "tracker-mcp"))
    assert values == {"MCP_NAME": "tracker-mcp", "MCP_DIR": "/opt/tracker-mcp", "MCP_VENV": "/opt/tracker-mcp/venv",
                      "MCP_CONF": "/etc/conf.d/tracker-mcp", "MCP_INITD": "/etc/init.d/tracker-mcp",
                      "MCP_LOG": "/var/log/tracker-mcp.log", "MCP_DEFAULT_PORT": "8779"}
    assert text == DEFAULT_MCP_INIT


def test_a_named_instance_has_its_own_mcp_service(tmp_path):
    values, text = run_mcp_fragments(["--instance", "school"], initd=str(tmp_path / "tracker-school-mcp"))
    assert values["MCP_NAME"] == "tracker-school-mcp" and values["MCP_DEFAULT_PORT"] == "8780"
    assert values["MCP_CONF"] == "/etc/conf.d/tracker-school-mcp" and values["MCP_VENV"] == "/opt/tracker-school-mcp/venv"
    assert 'command_args="/opt/tracker-school/mcp_http.py"' in text and "need net tracker-school;" in text
    assert 'command_user="tracker-school-mcp:tracker-school-mcp"' in text
    assert not re.search(r"/(opt|var/log)/tracker(?![-\w])", text) and not re.search(r"/opt/tracker-mcp\b", text)


def test_the_mcp_settings_live_in_their_own_config_and_reach_the_service():
    head = open(HEAD).read()
    mcp = set(re.findall(r'^add_setting "\$MCP_CONF" ([A-Z_]+) ', head, re.M))
    assert mcp == MCP_SETTINGS
    assert not mcp & set(re.findall(r'^add_setting "\$CONF" ([A-Z_]+) ', head, re.M)), "the token never goes in the app's config"
    exported = set(DEFAULT_MCP_INIT.split("export ", 1)[1].split("\n", 1)[0].split())
    assert exported == MCP_SETTINGS
    source = open(os.path.join(ROOT, "mcp_http.py")).read() + open(os.path.join(ROOT, "mcp_remote.py")).read()
    assert all(name in source for name in MCP_SETTINGS), "every setting is one the service reads"
    assert 'add_setting "$MCP_CONF" MCP_HTTP_BIND 127.0.0.1 ' in head and 'add_setting "$MCP_CONF" TRACKER_API_TOKEN "" ' in head
    assert 'chmod 600 "$MCP_CONF"; chown root:root "$MCP_CONF"' in head


def test_uninstall_and_upgrade_handle_the_mcp_service_too():
    text = open(HEAD).read()
    uninstall = text.split('if [ -n "$UNINSTALL" ]; then', 1)[1].split("exit 0", 1)[0]
    assert 'for svc in "$MCP_NAME" "$NAME"; do' in uninstall
    assert 'rm -f "$INITD" "$MCP_INITD" "$BACKUP_JOB"' in uninstall and 'rm -rf "$APP_DIR" "$MCP_DIR"' in uninstall
    assert 'rc-service "$MCP_NAME" stop >/dev/null 2>&1 || true\nrc-service "$NAME" stop' in text
    assert '"$MCP_VENV/bin/pip" install --no-cache-dir -q -r "$APP_DIR/requirements-mcp.txt"' in text


def test_the_payload_ships_the_mcp_requirements():
    build()
    shipped = payload_names(os.path.join(ROOT, "dist", "setup-tracker.sh"))
    assert {"tracker/requirements-mcp.txt", "tracker/mcp_server.py", "tracker/mcp_http.py", "tracker/mcp_remote.py"} <= shipped


def test_the_mcp_household_values_are_baked_but_never_a_token(tmp_path):
    repo = mini_repo(tmp_path, deploy_env="MCP_PUBLIC_HOSTS=mcp.example.com\nCF_ACCESS_AUD=aud-mcp\nMCP_ALLOWED_EMAILS=alex@example.com\n"
                                          "TRACKER_API_TOKEN=gt_never_baked\n")
    build(repo)  # no requirements-mcp.txt in the mini repo: the build still works
    out = open(os.path.join(repo, "dist", "setup-tracker.sh")).read()
    assert ': "${MCP_PUBLIC_HOSTS:=mcp.example.com}"' in out and ': "${CF_ACCESS_AUD:=aud-mcp}"' in out
    assert ': "${MCP_ALLOWED_EMAILS:=alex@example.com}"' in out
    assert "gt_never_baked" not in out
    subprocess.run(["sh", "-n", os.path.join(repo, "dist", "setup-tracker.sh")], check=True)


def test_the_wishlist_url_is_baked_but_never_its_token(tmp_path):
    repo = mini_repo(tmp_path, deploy_env="WISHLIST_URL=http://192.0.2.10:8090\nWISHLIST_TOKEN=wl_never_baked\n")
    build(repo)
    out = open(os.path.join(repo, "dist", "setup-tracker.sh")).read()
    assert ': "${WISHLIST_URL:=http://192.0.2.10:8090}"' in out
    assert "wl_never_baked" not in out
    head = open(HEAD).read()
    assert 'add_setting "$CONF" WISHLIST_TOKEN "" ' in head  # the installer writes it empty: the owner fills it in
    subprocess.run(["sh", "-n", os.path.join(repo, "dist", "setup-tracker.sh")], check=True)
