#!/usr/bin/env python3
"""Gift Tracker MCP over Streamable HTTP, behind Cloudflare Access: the `<service>-mcp` service the installer adds.

The tools are mcp_server.py's, built by build_server() for the scope of the one token this service holds
(TRACKER_API_TOKEN, asked of GET /api/v1/me at start). mcp_remote.py (a verbatim copy of mcp-server-shared's
remote.py, as Matinee has) checks the Access JWT on every request, checks the Host, and answers /healthz.

The service reaches the tracker on loopback (TRACKER_URL, else 127.0.0.1 on TRACKER_PORT) with the bearer token and
never opens the database. It refuses to start (exit 2, one line on stderr) without a token, with a token the tracker
rejects, or with HTTP settings mcp_remote finds unsafe.

Settings (environment; the installer writes them to /etc/conf.d/<service>-mcp):
  TRACKER_API_TOKEN      an agent token from the app (Settings > Agent tokens); its scope decides the tools
  TRACKER_URL            default http://127.0.0.1:<TRACKER_PORT or 8767>
  MCP_HTTP_BIND, MCP_HTTP_PORT, MCP_HTTP_PATH, MCP_PUBLIC_HOSTS, CF_ACCESS_TEAM_DOMAIN (else CF_TEAM_DOMAIN),
  CF_ACCESS_AUD, MCP_ALLOWED_EMAILS: see mcp_remote.py
"""
import argparse
import logging
import os
import sys
import time
import urllib.request
from collections.abc import Mapping
from typing import TYPE_CHECKING

import anyio.to_thread

import mcp_remote

if TYPE_CHECKING:
    import mcp_server

DEFAULT_PORT = 8779
DEFAULT_PATH = "/tracker/mcp"


def tracker_url(env: Mapping[str, str]) -> str:
    return ((env.get("TRACKER_URL") or "").strip()
            or f"http://127.0.0.1:{(env.get('TRACKER_PORT') or '8767').strip()}").rstrip("/")


def api_token(env: Mapping[str, str]) -> str:
    return (env.get("TRACKER_API_TOKEN") or "").strip()


def tracker_client(env: Mapping[str, str]) -> "mcp_server.TrackerClient":
    """The REST client the tools use: loopback URL and the token, never the database."""
    import mcp_server

    return mcp_server.TrackerClient(api_url=tracker_url(env), token=api_token(env))


def tracker_healthy(base_url: str) -> bool:
    """Blocking GET {base_url}/healthz; True only on HTTP 200 within 2 s. Loopback: never through a proxy."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{base_url.rstrip('/')}/healthz", timeout=2) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def _http_config(env: Mapping[str, str]) -> "mcp_remote.HttpConfig":
    env = dict(env)
    if not (env.get("CF_ACCESS_TEAM_DOMAIN") or "").strip():
        env["CF_ACCESS_TEAM_DOMAIN"] = env.get("CF_TEAM_DOMAIN", "")
    parser = argparse.ArgumentParser()
    mcp_remote.add_http_arguments(parser, default_port=DEFAULT_PORT, default_path=DEFAULT_PATH)
    return mcp_remote.http_config(parser.parse_args([]), env)


def _health_check(base: str):
    async def health() -> bool:
        return await anyio.to_thread.run_sync(tracker_healthy, base)

    return health


def build_http_app(env: Mapping[str, str], scope: str, verifier: "mcp_remote.AccessVerifier | None" = None):
    """The ASGI app for the given settings and token scope. `verifier` is injected by tests."""
    import mcp_server

    cfg = _http_config(env)
    server = mcp_server.build_server(scope)
    if server is None:
        raise mcp_remote.ConfigError("The MCP SDK is not installed: pip install -r requirements-mcp.txt")
    return mcp_remote.build_app(server, cfg, verifier, health_check=_health_check(tracker_url(env)))


def token_scope(client: "mcp_server.TrackerClient", attempts: int = 5, wait: float = 2.0) -> str:
    """The token's scope from /api/v1/me. Waits for a tracker that is still starting; a rejected token fails at once."""
    import mcp_server

    for attempt in range(attempts):
        try:
            return client.whoami()["scope"]
        except mcp_server.TrackerError as e:
            if e.status is not None or attempt == attempts - 1:
                raise
            time.sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


def main(argv: list | None = None, env: Mapping[str, str] | None = None, *, attempts: int = 5, wait: float = 2.0) -> int:
    """Run the service. Returns 2 with a one-line stderr message when it must not start."""
    env = dict(os.environ if env is None else env)
    if not api_token(env):
        print("tracker-mcp: TRACKER_API_TOKEN is empty. Make a token in the app (Settings > Agent tokens), put it in this "
              "service's config and restart it.", file=sys.stderr)
        return 2
    try:
        cfg = _http_config(env)
        cfg.check()
    except mcp_remote.ConfigError as exc:
        print(f"tracker-mcp: {exc}", file=sys.stderr)
        return 2
    import mcp_server

    client = tracker_client(env)
    try:
        scope = token_scope(client, attempts, wait)
    except mcp_server.TrackerError as exc:
        print(f"tracker-mcp: {exc}", file=sys.stderr)
        return 2
    mcp_server._default_client = client
    server = mcp_server.build_server(scope)
    if server is None:
        print("tracker-mcp: the MCP SDK is not installed: pip install -r requirements-mcp.txt", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, force=True, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("tracker.mcp").info("tracker-mcp starting: %s on %s:%s (scope %s)", cfg.path, cfg.bind, cfg.port, scope)
    mcp_remote.serve_http(server, cfg, health_check=_health_check(tracker_url(env)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
