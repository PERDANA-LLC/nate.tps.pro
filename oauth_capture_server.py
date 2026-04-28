
"""Background OAuth redirect capture server for Schwab."""
import ssl, sys, json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

CERT = Path("/Volumes/181TB/Perdana-LLC/nate.tps.pro/tokens/oauth_cert.pem")
KEY  = Path("/Volumes/181TB/Perdana-LLC/nate.tps.pro/tokens/oauth_key.pem")
HOST = "127.0.0.1"
PORT = 8182

CAPTURED = []

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        CAPTURED.append(self.path)
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body><h1>Authorization Received</h1><p>You may close this window.</p></body></html>")

    def log_message(self, format, *args):
        pass  # silent

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(str(CERT), str(KEY))
server = HTTPServer((HOST, PORT), Handler)
server.socket = ctx.wrap_socket(server.socket, server_side=True)

# Signal ready
print("SERVER_READY", flush=True)

# Wait for request (timeout after 200s)
server.timeout = 200
try:
    server.handle_request()
except:
    pass

if CAPTURED:
    print(f"CAPTURED:{CAPTURED[0]}", flush=True)
else:
    print("TIMEOUT", flush=True)
