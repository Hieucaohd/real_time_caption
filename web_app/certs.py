from __future__ import annotations

import datetime
import ipaddress
from pathlib import Path


def ensure_cert(folder: Path, ips: list[str]) -> tuple[Path, Path, Path]:
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as exc:
        raise RuntimeError("Install web requirements first: setup_web.bat") from exc
    folder.mkdir(parents=True, exist_ok=True)
    ca_key_path, ca_path = folder / "ca-key.pem", folder / "RealTimeCaption-Web-CA.crt"
    key_path, cert_path = folder / "server-key.pem", folder / "server-cert.pem"
    now = datetime.datetime.now(datetime.timezone.utc)
    if ca_key_path.exists() and ca_path.exists():
        ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
        ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
    else:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Real-time Caption Web Local CA")])
        ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=1))
              .not_valid_after(now+datetime.timedelta(days=3650))
              .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
              .sign(ca_key, hashes.SHA256()))
        ca_key_path.write_bytes(ca_key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ips[0])])
    sans = [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(ca.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=1))
            .not_valid_after(now+datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False).sign(ca_key, hashes.SHA256()))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path, ca_path
