"""Run the vision service: operator page on 127.0.0.1, phone camera page on the LAN."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import ipaddress
import socket
from pathlib import Path

import uvicorn

from .app import operator_app, phone_app
from .config import Settings
from .frames import UrlPoller
from .service import VisionService

CERT_DIR = Path(".certs")


def lan_addresses() -> list[str]:
    found: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith("127."))


def ensure_certificate(addresses: list[str]) -> tuple[Path, Path]:
    """Self-signed certificate so the phone browser allows live camera access."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    cert_path, key_path = CERT_DIR / "phone.crt", CERT_DIR / "phone.key"
    marker = CERT_DIR / "addresses.txt"
    wanted = ",".join(addresses)
    if cert_path.exists() and key_path.exists() and marker.exists() and marker.read_text() == wanted:
        return cert_path, key_path
    CERT_DIR.mkdir(exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "grokarm-vision")])
    alt = [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(a)) for a in addresses]
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    marker.write_text(wanted)
    return cert_path, key_path


async def serve(settings: Settings) -> None:
    service = VisionService(settings)
    poller = None
    servers = [
        uvicorn.Server(uvicorn.Config(operator_app(service), host="127.0.0.1", port=settings.operator_port,
                                      log_level="warning"))
    ]
    if settings.camera_source == "url":
        if not settings.camera_url:
            raise SystemExit("CAMERA_SOURCE=url needs CAMERA_URL, e.g. http://192.168.1.23:8080")
        poller = UrlPoller(service.store, settings.camera_url, settings.poll_interval_s)
        poller.start()
    else:
        addresses = lan_addresses()
        ssl = {}
        if settings.phone_https:
            cert, key = ensure_certificate(addresses)
            ssl = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
        servers.append(
            uvicorn.Server(uvicorn.Config(phone_app(service), host="0.0.0.0", port=settings.phone_port,
                                          log_level="warning", **ssl))
        )
        scheme = "https" if settings.phone_https else "http"
        print("Phone camera page (open on the phone, same Wi-Fi):")
        for a in addresses or ["<laptop-ip>"]:
            print(f"  {scheme}://{a}:{settings.phone_port}/")
    print(f"Operator page: http://127.0.0.1:{settings.operator_port}/")
    print(f"Resolver: {service.resolver.name}")
    try:
        await asyncio.gather(*(s.serve() for s in servers))
    finally:
        if poller:
            poller.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m grokarm_vision")
    parser.add_argument("--port", type=int, help="operator port (default VISION_PORT or 8710)")
    parser.add_argument("--phone-port", type=int, help="phone port (default VISION_PHONE_PORT or 8711)")
    parser.add_argument("--no-https", action="store_true", help="serve the phone page over plain HTTP")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.port:
        settings.operator_port = args.port
    if args.phone_port:
        settings.phone_port = args.phone_port
    if args.no_https:
        settings.phone_https = False
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
