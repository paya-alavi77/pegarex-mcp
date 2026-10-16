"""Sign-in for the bridge: the hosted PegaRex MCP server asks for a free PegaRex account.

The server speaks OAuth 2.1, the way the MCP authorization spec describes it. The
first time it is needed, the bridge registers itself with the server (RFC 7591),
opens the browser on PegaRex's sign-in page and waits on 127.0.0.1 for the
answer; PKCE (S256) and a random state protect that step. The login is then
saved in one file (see `default_home`) and renewed silently: the access token
lasts an hour, the refresh token 30 days without use.

Several copies of the bridge can run at once (one per Claude Code session, for
example) and they share the saved login. The server rotates refresh tokens and
treats a spent one presented again as stolen, ending the whole login, so a copy
renews only while holding a lock on the file, after checking whether another
copy already did.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import socket
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

CLIENT_NAME = "pegarex-mcp"
# Time to sign in (or create the account) and allow, in the browser.
LOGIN_TIMEOUT = float(os.environ.get("PEGAREX_MCP_LOGIN_TIMEOUT") or 600)
# Another copy may be in the middle of a sign-in while holding the lock.
LOCK_TIMEOUT = LOGIN_TIMEOUT + 60
# Renew a minute before the access token runs out.
EARLY = 60


class LoginError(Exception):
    """The bridge could not sign in. The message is for the person."""


def say(message: str) -> None:
    """Tell the person. stdout is the MCP channel, so this goes to stderr."""
    print(f"pegarex-mcp: {message}", file=sys.stderr, flush=True)


def default_home() -> Path:
    """Where the login is saved: PEGAREX_MCP_HOME, else the user's config folder."""
    if os.environ.get("PEGAREX_MCP_HOME"):
        return Path(os.environ["PEGAREX_MCP_HOME"]).expanduser()
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "pegarex-mcp"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "pegarex-mcp"


# ── the saved login ───────────────────────────────────────────────────────

def _try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


class Store:
    """The saved login, one entry per server URL, in <home>/auth.json (mode 0600)."""

    def __init__(self, url: str, home: Path | None = None) -> None:
        self.url = url
        self.home = home or default_home()
        self.path = self.home / "auth.json"

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def read(self) -> dict[str, Any]:
        servers = self._load().get("servers")
        entry = servers.get(self.url) if isinstance(servers, dict) else None
        return entry if isinstance(entry, dict) else {}

    def write(self, entry: dict[str, Any] | None) -> None:
        """Replace this server's entry (None removes it). Call with the lock held."""
        data = self._load()
        servers = data.get("servers") if isinstance(data.get("servers"), dict) else {}
        if entry:
            servers[self.url] = entry
        else:
            servers.pop(self.url, None)
        data["servers"] = servers
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.home / f".auth-{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    @contextlib.asynccontextmanager
    async def locked(self) -> AsyncIterator[None]:
        """Hold the lock that every copy of the bridge on this computer shares."""
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.home / "auth.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            deadline = time.monotonic() + LOCK_TIMEOUT
            while not _try_lock(fd):
                if time.monotonic() > deadline:
                    raise LoginError("another copy of pegarex-mcp held the login for too long; try again")
                await asyncio.sleep(0.2)
            try:
                yield
            finally:
                _unlock(fd)
        finally:
            os.close(fd)


def _usable(tokens: dict[str, Any]) -> bool:
    return bool(tokens.get("access_token")) and time.time() < float(tokens.get("expires_at") or 0) - EARLY


def _saved(body: Any) -> dict[str, Any]:
    """A token response as it is saved: the expiry made absolute."""
    if not isinstance(body, dict) or not body.get("access_token"):
        raise LoginError("the sign-in server answered without a token")
    return {"access_token": body["access_token"], "refresh_token": body.get("refresh_token"),
            "expires_at": time.time() + float(body.get("expires_in") or 3600), "scope": body.get("scope")}


# ── talking to the sign-in server ─────────────────────────────────────────

def _oauth_http() -> httpx.AsyncClient:
    # Token and discovery requests never follow redirects.
    return httpx.AsyncClient(timeout=30, follow_redirects=False)


def _checked(url: str) -> str:
    """Codes and tokens travel only over https, or to this computer."""
    u = urlparse(url)
    if u.scheme == "https" or (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1")):
        return url
    raise LoginError(f"refusing to sign in over an insecure address: {url}")


def _with_secret(data: dict[str, str], client: dict[str, Any]) -> dict[str, str]:
    if client.get("client_secret") and client.get("auth_method") == "client_secret_post":
        return dict(data, client_secret=client["client_secret"])
    return data


async def _get_json(http: httpx.AsyncClient, url: str) -> dict[str, Any]:
    r = await http.get(_checked(url), headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise LoginError(f"could not read the sign-in settings at {url} (HTTP {r.status_code})")
    try:
        data = r.json()  # whatever the Content-Type says
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise LoginError(f"the sign-in settings at {url} are not valid")
    return data


async def _discover(http: httpx.AsyncClient, url: str, www_authenticate: str | None) -> dict[str, Any]:
    """The protected resource's metadata (RFC 9728), then its sign-in server's (RFC 8414)."""
    u = urlparse(url)
    hint = re.search(r'resource_metadata="([^"]+)"', www_authenticate or "")
    prm = await _get_json(http, hint.group(1) if hint else
                          f"{u.scheme}://{u.netloc}/.well-known/oauth-protected-resource{u.path.rstrip('/')}")
    resource = str(prm.get("resource") or url)
    if resource.rstrip("/") != url.rstrip("/"):
        raise LoginError(f"the server's sign-in settings are for {resource}, not {url}")
    servers = prm.get("authorization_servers") or []
    if not servers:
        raise LoginError("the server does not say where to sign in")
    issuer = str(servers[0]).rstrip("/")
    i = urlparse(issuer)
    asm = await _get_json(http, f"{i.scheme}://{i.netloc}/.well-known/oauth-authorization-server{i.path}")
    if str(asm.get("issuer") or "").rstrip("/") != issuer:
        raise LoginError("the sign-in server's settings name another server")
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        if not asm.get(key):
            raise LoginError(f"the sign-in server does not publish its {key}")
        _checked(asm[key])
    return {"issuer": issuer, "resource": resource,
            "scope": " ".join(prm.get("scopes_supported") or []) or None,
            "authorization_endpoint": asm["authorization_endpoint"],
            "token_endpoint": asm["token_endpoint"],
            "registration_endpoint": asm["registration_endpoint"],
            "revocation_endpoint": asm.get("revocation_endpoint")}


async def _register(http: httpx.AsyncClient, meta: dict[str, Any], port: int) -> dict[str, Any]:
    body: dict[str, Any] = {
        "client_name": CLIENT_NAME,
        "client_uri": "https://github.com/paya-alavi77/pegarex-mcp",
        "redirect_uris": [f"http://127.0.0.1:{port}/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    if meta.get("scope"):
        body["scope"] = meta["scope"]
    r = await http.post(meta["registration_endpoint"], json=body)
    if r.status_code not in (200, 201):
        raise LoginError(f"could not register with the sign-in server (HTTP {r.status_code})")
    info = r.json()
    return {"client_id": info["client_id"], "client_secret": info.get("client_secret"),
            "auth_method": info.get("token_endpoint_auth_method") or "none", "port": port,
            **{k: meta[k] for k in ("issuer", "resource", "scope", "authorization_endpoint",
                                    "token_endpoint", "revocation_endpoint")}}


# ── the browser step ──────────────────────────────────────────────────────

_PAGE = """<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>PegaRex</title>
<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem;line-height:1.5">
<h1 style="font-size:1.4rem">{title}</h1><p>{pt}</p><p lang="en" style="color:#555">{en}</p></body></html>"""
PAGE_DONE = _PAGE.format(title="PegaRex conectado",
                         pt="Pode fechar esta aba e voltar ao seu aplicativo.",
                         en="PegaRex is connected. You can close this tab and go back to your app.")
PAGE_CANCELLED = _PAGE.format(title="Conexão cancelada",
                              pt="Nada foi conectado. Pode fechar esta aba.",
                              en="Nothing was connected. You can close this tab.")
PAGE_UNKNOWN = _PAGE.format(title="PegaRex", pt="Este endereço não espera nada agora.",
                            en="Nothing is expected at this address now.")


def _callback_socket(port: int) -> socket.socket | None:
    """Listen on 127.0.0.1:<port> (0: any free port); None if the port is taken."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name != "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.listen(8)
        s.setblocking(False)
        return s
    except OSError:
        s.close()
        return None


async def _send_page(writer: asyncio.StreamWriter, status: int, html: str) -> None:
    body = html.encode()
    head = (f"HTTP/1.1 {status} {'OK' if status == 200 else 'Not Found'}\r\n"
            "Content-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n")
    writer.write(head.encode() + body)
    await writer.drain()


async def _browser_code(client: dict[str, Any], sock: socket.socket) -> tuple[str, str]:
    """Send the person to the sign-in page and wait on 127.0.0.1 for the code.
    Returns the code and the PKCE verifier."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    params = {"response_type": "code", "client_id": client["client_id"],
              "redirect_uri": f"http://127.0.0.1:{client['port']}/callback",
              "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
              "resource": client["resource"]}
    if client.get("scope"):
        params["scope"] = client["scope"]
    url = f"{client['authorization_endpoint']}?{urlencode(params)}"
    answer: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def on_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
            while (await asyncio.wait_for(reader.readline(), 10)) not in (b"\r\n", b"\n", b""):
                pass
            parts = line.split()
            target = urlparse(parts[1].decode("latin-1") if len(parts) > 1 else "/")
            q = parse_qs(target.query)
            given = q.get("state", [""])[0].encode("utf-8", "replace")
            # Anything that isn't the answer to this sign-in is ignored.
            if target.path != "/callback" or not secrets.compare_digest(given, state.encode()):
                await _send_page(writer, 404, PAGE_UNKNOWN)
            elif q.get("code"):
                await _send_page(writer, 200, PAGE_DONE)
                if not answer.done():
                    answer.set_result(q["code"][0])
            else:
                await _send_page(writer, 200, PAGE_CANCELLED)
                if not answer.done():
                    answer.set_exception(LoginError("the connection was cancelled on the sign-in page"))
        except (asyncio.TimeoutError, OSError, ValueError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(on_request, sock=sock)
    async with server:
        say("PegaRex asks you to sign in once with a free PegaRex account; opening your browser.")
        say(f"if it does not open, go to: {url}")
        if not await asyncio.to_thread(webbrowser.open, url):
            say("no browser could be opened here: open the address above in a browser on this "
                "computer, or sign in where one is available (`pegarex-mcp login`).")
        try:
            code = await asyncio.wait_for(answer, LOGIN_TIMEOUT)
        except asyncio.TimeoutError:
            wait = f"{int(LOGIN_TIMEOUT // 60)} minutes" if LOGIN_TIMEOUT >= 120 else f"{int(LOGIN_TIMEOUT)} seconds"
            raise LoginError(f"no answer from the sign-in page in {wait}; "
                             "run `pegarex-mcp login` to try again") from None
    return code, verifier


# ── the login, for httpx ──────────────────────────────────────────────────

class PegaRexAuth(httpx.Auth):
    """Adds the saved login to every request to the MCP server and renews it when
    needed. A 401 means a renewal or, failing that, a sign-in in the browser."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.signed_in = False            # a browser sign-in happened in this process
        self._lock: asyncio.Lock | None = None

    def sync_auth_flow(self, request: httpx.Request):  # pragma: no cover - the bridge is async
        raise RuntimeError("PegaRexAuth works with httpx.AsyncClient only")

    async def async_auth_flow(self, request: httpx.Request):
        tokens = self.store.read().get("tokens") or {}
        token = tokens.get("access_token")
        if token and not _usable(tokens):
            token = await self.renew(token)
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code != 401:
            return
        token = await self.renew(token, response.headers.get("www-authenticate"))
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
            yield request

    async def renew(self, failed: str | None, www_authenticate: str | None = None) -> str:
        """A working access token in place of `failed` (None if there was none)."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock, self.store.locked():
            entry = self.store.read()
            tokens = entry.get("tokens") or {}
            # Another request, or another copy of the bridge, renewed it already.
            if tokens.get("access_token") and tokens["access_token"] != failed and _usable(tokens):
                return tokens["access_token"]
            if tokens.get("refresh_token") and entry.get("client"):
                token = await self._refresh(entry)
                if token:
                    return token
            return await self._sign_in(entry, www_authenticate)

    async def _refresh(self, entry: dict[str, Any]) -> str | None:
        client = entry["client"]
        data = {"grant_type": "refresh_token", "refresh_token": entry["tokens"]["refresh_token"],
                "client_id": client["client_id"], "resource": client["resource"]}
        async with _oauth_http() as http:
            r = await http.post(_checked(client["token_endpoint"]), data=_with_secret(data, client))
        if r.status_code == 200:
            entry["tokens"] = _saved(r.json())
            self.store.write(entry)
            return entry["tokens"]["access_token"]
        if r.status_code >= 500 or r.status_code == 429:
            raise LoginError(f"the PegaRex server could not renew the login now (HTTP {r.status_code}); "
                             "try again in a moment")
        # Spent, revoked or 30 days unused: only a new sign-in helps. A client the
        # server no longer knows registers again.
        entry.pop("tokens", None)
        with contextlib.suppress(ValueError, AttributeError):
            if r.json().get("error") == "invalid_client":
                entry.pop("client", None)
        self.store.write(entry)
        return None

    async def _sign_in(self, entry: dict[str, Any], www_authenticate: str | None) -> str:
        async with _oauth_http() as http:
            meta = await _discover(http, self.store.url, www_authenticate)
            client = entry.get("client")
            if not client or any(client.get(k) != meta[k] for k in ("issuer", "token_endpoint", "resource")):
                client = None
            # The registered return address names a port; if it is taken now, register again.
            sock = _callback_socket(client["port"]) if client else None
            if sock is None:
                client, sock = None, _callback_socket(0)
                if sock is None:
                    raise LoginError("could not open a local port for the sign-in answer")
            try:
                if client is None:
                    client = await _register(http, meta, sock.getsockname()[1])
                    entry = {"client": client}
                    self.store.write(entry)
                code, verifier = await _browser_code(client, sock)
            finally:
                sock.close()
            r = await http.post(_checked(client["token_endpoint"]), data=_with_secret({
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": f"http://127.0.0.1:{client['port']}/callback",
                "client_id": client["client_id"], "code_verifier": verifier,
                "resource": client["resource"]}, client))
        if r.status_code != 200:
            raise LoginError(f"the sign-in did not complete (HTTP {r.status_code}); "
                             "run `pegarex-mcp login` to try again")
        entry["tokens"] = _saved(r.json())
        self.store.write(entry)
        self.signed_in = True
        say("signed in; the login is saved and renews on its own.")
        return entry["tokens"]["access_token"]


async def logout(store: Store) -> str:
    """Forget the saved login and end it on the server (RFC 7009). Returns "none"
    if there was no login, "ended" if the server ended it too, "local" if only
    the saved copy is gone (the server could not be reached or refused)."""
    async with store.locked():
        entry = store.read()
        tokens = entry.pop("tokens", None)
        store.write(entry)          # the app's registration stays, for the next login
    if not tokens:
        return "none"
    client = entry.get("client") or {}
    if not (client.get("revocation_endpoint") and tokens.get("refresh_token")):
        return "local"
    data = _with_secret({"token": tokens["refresh_token"], "token_type_hint": "refresh_token",
                         "client_id": client["client_id"]}, client)
    # The server (mcp SDK 2.x) wants the field even from a client without a secret,
    # and ignores it for those.
    data.setdefault("client_secret", "")
    try:
        async with _oauth_http() as http:
            r = await http.post(_checked(client["revocation_endpoint"]), data=data)
    except (httpx.HTTPError, LoginError):
        return "local"
    return "ended" if r.status_code == 200 else "local"
