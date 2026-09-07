"""A self-signed certificate for the manager.

Not about eavesdropping. The streaming client refuses to run outside a browser
"secure context", and plain HTTP only counts as one on localhost -- so served
over HTTP at a LAN address the page loads and then sits there, unable to open
its WebSocket. Serving HTTPS, even on a certificate nobody trusts, is what makes
a session work from another machine at all.

Behind a reverse proxy that terminates TLS, set MANAGER_ENABLE_HTTPS=false and
let the proxy present a real certificate.
"""
from __future__ import annotations

import datetime
import ipaddress
import logging
import socket
import ssl
import urllib.parse
from pathlib import Path

log = logging.getLogger("tls")


def _san_entries(hosts: list[str]):
    from cryptography import x509
    out, seen = [], set()
    for h in hosts:
        h = h.strip()
        if not h or h in seen:
            continue
        seen.add(h)
        try:
            out.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            out.append(x509.DNSName(h))
    return out


def candidate_hosts(public_url: str, extra: str) -> list[str]:
    """Everything this manager might reasonably be reached as.

    A certificate that does not name the address a player types produces a
    second browser warning on top of the untrusted-issuer one, so name as much
    as can be known: loopback, the host's own name, whatever PUBLIC_URL says,
    and anything the operator adds by hand.
    """
    hosts = ["localhost", "127.0.0.1", "::1"]
    try:
        hosts.append(socket.gethostname())
    except OSError:
        pass
    if public_url:
        parsed = urllib.parse.urlsplit(public_url if "//" in public_url
                                       else f"//{public_url}")
        if parsed.hostname:
            hosts.append(parsed.hostname)
    hosts.extend(h for h in extra.split(",") if h.strip())
    return hosts


def ensure_certificate(state_dir: str, hosts: list[str]) -> tuple[Path, Path]:
    """Return (cert, key), generating a self-signed pair on first run."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert_path = Path(state_dir) / "manager-cert.pem"
    key_path = Path(state_dir) / "manager-key.pem"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Chaos Overlords manager")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(_san_entries(hosts)), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()))
    key_path.chmod(0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    log.info("generated a self-signed certificate for: %s", ", ".join(hosts))
    return cert_path, key_path


def context(state_dir: str, public_url: str, extra_hosts: str,
            cert: str = "", key: str = "") -> ssl.SSLContext:
    if cert and key:
        cert_path, key_path = Path(cert), Path(key)
        log.info("using the certificate at %s", cert_path)
    else:
        cert_path, key_path = ensure_certificate(
            state_dir, candidate_hosts(public_url, extra_hosts))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    return ctx
