"""
Schwab API Client wrapper for Project Nate — TPS Scanner.
Provides a singleton get_client() that reads credentials from .env,
creates a schwabdev Client, and handles OAuth token lifecycle
with an auto-capture local HTTPS server (call_on_auth).
"""

from __future__ import annotations

import logging
import os
import socket
import ssl
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

log = logging.getLogger(__name__)

_client = None
_lock = threading.Lock()


# ── helpers ──────────────────────────────────────────────────────
def _proj_root() -> Path:
    return Path(__file__).resolve().parent


def _required_env(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise EnvironmentError(f"{key} not set in .env. Copy .env.example or add it.")
    return val


# ── self-signed cert generation ──────────────────────────────────
def _ensure_self_signed_cert(cert_path: Path, key_path: Path):
    """Generate a self-signed cert + key pair if missing."""
    if cert_path.exists() and key_path.exists():
        return

    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "CA"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Localhost"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ProjectNate-OAuth"),
        x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1"),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("127.0.0.1"),
                x509.DNSName("localhost"),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.chmod(0o600)
    log.info("Generated self-signed cert: %s / %s", cert_path, key_path)


# ── OAuth auto‑capture server ────────────────────────────────────
def _make_oauth_callback(callback_url: str) -> callable:
    """Return a call_on_auth function that performs headless OAuth.

    Starts a local HTTPS server on the port specified in *callback_url*,
    opens the browser to the Schwab auth page, and captures the redirect
    containing the authorization ``code``.

    Returns
    -------
    callable
        ``(auth_url: str) -> str``  — returns the full callback URL.
    """
    parsed = urlparse(callback_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    scheme = parsed.scheme or "https"

    # Ensure certs exist
    root = _proj_root()
    cert_dir = root / "tokens"
    cert_path = cert_dir / "oauth_cert.pem"
    key_path = cert_dir / "oauth_key.pem"
    _ensure_self_signed_cert(cert_path, key_path)

    def _oauth_callback(auth_url: str) -> str:
        """Block until OAuth redirect is captured, then return the callback URL."""
        captured: list[str] = []
        ready = threading.Event()
        error_msg: list[str] = []

        from http.server import HTTPServer, BaseHTTPRequestHandler

        class _OAuthHandler(BaseHTTPRequestHandler):
            def do_GET(self_):  # noqa: N805
                captured.append(f"{scheme}://{host}:{port}{self_.path}")
                self_.send_response(200)
                self_.send_header("Content-type", "text/html; charset=utf-8")
                self_.end_headers()
                self_.wfile.write(
                    b"<!DOCTYPE html><html><body style='font-family:sans-serif;"
                    b"text-align:center;padding-top:3em'>"
                    b"<h2>&#x2705; Authorization Complete</h2>"
                    b"<p>You may close this tab and return to the terminal.</p>"
                    b"</body></html>"
                )
                # Shutdown in a separate thread to avoid deadlock
                threading.Thread(target=self_.server.shutdown, daemon=True).start()

            def log_message(self_, *args):  # noqa: N805
                pass  # silence HTTP logs

        # ── create & start server (with sudo fallback for privileged ports) ──
        try:
            server = HTTPServer((host, port), _OAuthHandler)
        except PermissionError:
            if port < 1024:
                return _oauth_via_sudo(auth_url, host, port, scheme, cert_path, key_path)
            raise

        # Wrap with TLS
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)

        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        ready.set()

        # ── open browser ──
        import webbrowser
        log.info("Opening browser for Schwab OAuth …")
        webbrowser.open(auth_url)
        print(f"[Schwab OAuth] Waiting for authorization on {scheme}://{host}:{port} …")
        print(f"[Schwab OAuth] If the browser does not open, visit:\n    {auth_url}")

        # ── wait (max 180 s) ──
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if captured:
                server_thread.join(timeout=2)
                try:
                    server.server_close()
                except Exception:
                    pass
                return captured[0]
            time.sleep(0.5)

        # Timeout
        try:
            server.server_close()
        except Exception:
            pass
        raise TimeoutError(
            "OAuth timed out after 180 s — browser redirect was not received.\n"
            "Make sure Schwab's redirect URI matches this server's address.\n"
            f"Expected: {callback_url}"
        )

    return _oauth_callback


def _oauth_via_sudo(auth_url: str, host: str, port: int, scheme: str,
                    cert_path: Path, key_path: Path) -> str:
    """Run the OAuth HTTPS server with sudo for privileged ports (<1024).

    Writes a small temporary script, executes it via ``sudo python3``,
    and captures the callback URL from its stdout.
    """
    import tempfile
    import webbrowser

    server_script = f'''
import ssl, sys, json
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

CERT = {str(cert_path)!r}
KEY  = {str(key_path)!r}
HOST = {host!r}
PORT = {port}

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        # Print captured URL to stdout for parent to read
        print({scheme!r} + "://" + HOST + ":" + str(PORT) + self.path, flush=True)
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h2>OK</h2>")
        Thread(target=self.server.shutdown, daemon=True).start()
    def log_message(self, *a): pass

srv = HTTPServer((HOST, PORT), H)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(CERT, KEY)
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)

# Signal parent that we're ready (print marker)
print("READY", flush=True)
srv.serve_forever(poll_interval=0.5)
'''

    fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="schwab_oauth_")
    try:
        os.write(fd, server_script.encode())
        os.close(fd)
        os.chmod(tmp_path, 0o500)

        print(f"[Schwab OAuth] Port {port} needs elevated privileges — requesting sudo …")
        proc = subprocess.Popen(
            ["sudo", sys.executable, tmp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Wait for server to be ready
        ready_line = proc.stdout.readline()
        if "READY" not in ready_line:
            stderr_tail = proc.stderr.read()[-500:]
            proc.terminate()
            raise RuntimeError(
                f"sudo server failed to start. stderr: {stderr_tail}"
            )

        # Open browser
        log.info("Opening browser for Schwab OAuth …")
        webbrowser.open(auth_url)
        print(f"[Schwab OAuth] Waiting for authorization on {scheme}://{host}:{port} …")
        print(f"[Schwab OAuth] If the browser does not open, visit:\n    {auth_url}")

        # Read captured URL (blocks until server gets a hit)
        captured = proc.stdout.readline().strip()

        # Clean shutdown
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

        if captured and captured.startswith("https://"):
            return captured

        raise TimeoutError(
            "OAuth timed out — browser redirect was not received.\n"
            f"Expected callback on {scheme}://{host}:{port}"
        )

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── public API ───────────────────────────────────────────────────
def get_client():
    """Return a ready-to-use schwabdev ``Client`` singleton.

    On first call the OAuth flow runs **headlessly**: a local HTTPS server
    is started on the port given in ``SCHWAB_CALLBACK_URL`` (default 443),
    the browser opens automatically, and the authorization code is captured
    without manual copy‑pasting.
    """
    global _client
    if _client is not None:
        return _client

    with _lock:
        if _client is not None:
            return _client

        root = _proj_root()
        load_dotenv(root / ".env")

        app_key = _required_env("SCHWAB_CLIENT_ID")
        app_secret = _required_env("SCHWAB_CLIENT_SECRET")
        callback = _required_env("SCHWAB_CALLBACK_URL")

        token_path = str(root / "tokens")
        os.makedirs(token_path, exist_ok=True)

        from schwabdev import Client

        _client = Client(
            app_key=app_key,
            app_secret=app_secret,
            callback_url=callback,
            tokens_db=os.path.join(token_path, "schwab_tokens.db"),
            call_on_auth=_make_oauth_callback(callback),
            timeout=15,
        )

        log.info("SchwabClient: connected OK (tokens=%s)", token_path)
        return _client


# ── optional startup test ────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print(">>> Testing Schwab Client …")
    try:
        client = get_client()
    except PermissionError as e:
        if "443" in str(e) or "privileged" in str(e).lower():
            print(
                "⚠  Port 443 requires elevated privileges.\n"
                "   Run with:  sudo .venv/bin/python schwab_client.py\n"
                "   Or set SCHWAB_CALLBACK_URL=https://127.0.0.1:8182 in .env\n"
                "     and add https://127.0.0.1:8182 to your Schwab app redirect URIs."
            )
            sys.exit(1)
        raise

    resp = client.quote("SPY")
    if resp.ok:
        data = resp.json()
        price = data.get("SPY", {}).get("quote", {}).get("lastPrice", "?")
        print(f"✓ SPY quote: ${price}")
    else:
        print(f"✗ quote failed: {resp.status_code} {resp.text[:200]}")

    print(">>> Done.")