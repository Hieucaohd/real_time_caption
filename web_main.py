from __future__ import annotations

import argparse
import socket
import threading
from pathlib import Path

from web_app.certs import ensure_cert
from web_app.server import WebServer, serve
from web_app.store import DATA_DIR


def local_ips() -> list[str]:
    values = {"127.0.0.1"}
    try:
        values.update(x[4][0] for x in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    return sorted(values, key=lambda x: x.startswith("127."))


def main():
    parser = argparse.ArgumentParser(description="Standalone iPhone-first Real-time Caption web server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--http-port", type=int, default=8080, help="HTTP port used to download the CA certificate")
    parser.add_argument("--http", action="store_true", help="HTTP development mode (iPhone microphone will not work)")
    args = parser.parse_args()
    ips = local_ips()
    cert = key = None
    if not args.http:
        cert, key, _ca = ensure_cert(DATA_DIR / "certs", ips)
    server, service = serve(args.host, args.port, cert, key)
    scheme = "http" if args.http else "https"
    print("\nReal-time Caption Web is independent from the native app.")
    print("Keep Chrome remote debugging open only when using ChatGPT.")
    for ip in ips:
        print(f"  {scheme}://{ip}:{args.port}/?token={service.settings.token}")
    if not args.http:
        bootstrap = WebServer((args.host, args.http_port), service)
        threading.Thread(target=bootstrap.serve_forever, name="web-cert-bootstrap", daemon=True).start()
        for ip in ips:
            print(f"  CA certificate: http://{ip}:{args.http_port}/ca.crt")
    print("\nOn iPhone: download/install/trust the CA certificate first, then open the HTTPS URL.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        service.stop_capture()
        server.shutdown()


if __name__ == "__main__":
    main()
