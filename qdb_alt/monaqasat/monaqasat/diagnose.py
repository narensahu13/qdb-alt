"""Why won't Python connect when the browser can?

`SSLZeroReturnError ... TLS/SSL connection has been closed (EOF)` means the
server accepted the TCP connection and then hung up during the TLS handshake
without sending an alert. Several very different things cause that, and they
have different fixes, so this module tests them one at a time instead of
guessing.

  1. corporate TLS interception  -- the proxy re-signs traffic with a root
     certificate that Windows trusts and Python does not
  2. an outbound proxy Python is not using -- Chrome reads the system proxy,
     requests does not unless told
  3. a TLS version or cipher mismatch -- older server stacks reject what
     OpenSSL 3 offers by default
  4. the server declining non-browser clients outright
  5. something on the path answering in the server's place and refusing --
     a proxy returning 407, or the WAF's own page returned as HTTP 200

Case 5 is the one worth stating plainly: a blocked request still answers.
requests raises nothing, so anything that treats "got a response" as success
reports a healthy network while the crawler cannot fetch a single page.

Run `python -m monaqasat doctor` and read the verdict at the bottom. It
exits 0 when the site is reachable or the fix is yours to apply, 1 when the
diagnosis is inconclusive, and 2 when the refusal is deliberate and there is
nothing on this side to configure.
"""

from __future__ import annotations

import os
import platform
import socket
import ssl
import sys
from urllib.parse import urlparse

HOST = "monaqasat.mof.gov.qa"
PORT = 443
TIMEOUT = 15


def _line(label: str, value: object) -> None:
    print(f"  {label:<34} {value}")


# --------------------------------------------------------------------------
# individual probes
# --------------------------------------------------------------------------

def tcp_reachable(host: str = HOST, port: int = PORT) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT):
            return True, "connected"
    except Exception as exc:                        # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def handshake(context: ssl.SSLContext, host: str = HOST,
              port: int = PORT) -> tuple[bool, str]:
    """One TLS handshake. Returns (ok, detail) and never raises."""
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                cipher = tls.cipher()
                peer = tls.getpeercert()
                issuer = ""
                if peer:
                    for part in peer.get("issuer", ()):
                        for k, v in part:
                            if k == "organizationName":
                                issuer = v
                return True, (f"{tls.version()}  {cipher[0] if cipher else '?'}"
                              + (f"  issuer={issuer}" if issuer else ""))
    except Exception as exc:                        # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def ctx_default() -> ssl.SSLContext:
    return ssl.create_default_context()


def ctx_no_verify() -> ssl.SSLContext:
    """Handshake without certificate verification.

    Diagnostic only -- it tells us whether the failure is about trust or
    about the handshake itself. Nothing in the crawler runs unverified.
    """
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def ctx_tls12() -> ssl.SSLContext:
    c = ssl.create_default_context()
    c.minimum_version = ssl.TLSVersion.TLSv1_2
    c.maximum_version = ssl.TLSVersion.TLSv1_2
    return c


def ctx_relaxed() -> ssl.SSLContext:
    """OpenSSL 3 raised the default security level; older server stacks fall
    below it. This drops to level 1, which is still verified TLS."""
    c = ssl.create_default_context()
    try:
        c.set_ciphers("DEFAULT@SECLEVEL=1")
    except ssl.SSLError:
        pass
    return c


def ctx_system_store() -> ssl.SSLContext | None:
    """The OS certificate store -- what Chrome uses, and what carries a
    corporate interception root when there is one."""
    try:
        import truststore
    except ImportError:
        return None
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


BLOCK_STATUSES = {
    401: "the server wants credentials",
    403: "forbidden -- the site, or something in front of it, refused",
    407: "the proxy requires authentication",
    451: "blocked for legal reasons",
    502: "the proxy could not reach the server",
    511: "the network requires sign-in (a captive portal)",
}


def block_reason(status: int, headers, text: str, *,
                 expect_text: bool = True) -> str:
    """Why this answer is not the page we asked for; "" when it is.

    A blocked request still answers. The WAF returns its own page with HTTP
    200, a proxy returns 407 or an interstitial, and requests raises nothing
    at all -- which is how `doctor` came to print "Everything works" on a
    network where the crawler could not fetch a single page.
    """
    from .parse import looks_rejected

    if looks_rejected(text):
        return "the firewall's block page, served as HTTP 200"
    if status in BLOCK_STATUSES:
        return f"HTTP {status} -- {BLOCK_STATUSES[status]}"
    if status != 200:
        return f"HTTP {status}"
    ctype = ""
    try:
        ctype = (headers.get("Content-Type") or "").lower()
    except AttributeError:                          # a plain mapping is fine
        ctype = str(headers or "").lower()
    if expect_text and ("html" in ctype or text.lstrip().startswith("<")):
        return ("HTTP 200, but the body is HTML where robots.txt should be "
                "plain text -- something answered in the server's place")
    return ""


def who_answered(headers) -> str:
    """Name the box that refused, when it identifies itself. IT needs this."""
    for h in ("Via", "X-Squid-Error", "X-Cache", "Proxy-Agent", "Server"):
        try:
            value = headers.get(h)
        except AttributeError:
            return ""
        if value:
            return f"{h}: {str(value)[:48]}"
    return ""


def http_get(use_proxy: bool, verify=True, path: str = "/robots.txt",
             host: str = HOST) -> tuple[bool, str, str]:
    """The layer the crawler actually uses.

    Returns (reached the real server, detail, reason it was not reached).
    A raw socket handshake ignores proxy settings; requests does not. When
    the socket works and this does not, the proxy is the problem.
    """
    import requests
    kwargs: dict = {"timeout": TIMEOUT, "verify": verify,
                    "headers": {"User-Agent": "Mozilla/5.0"}}
    if not use_proxy:
        kwargs["proxies"] = {"http": None, "https": None}
    try:
        r = requests.get(f"https://{host}{path}", **kwargs)
    except Exception as exc:                        # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:110]}", ""

    text = r.text or ""
    lines = text.strip().splitlines()
    first = lines[0][:40] if lines else ""
    reason = block_reason(r.status_code, r.headers, text,
                          expect_text=path.endswith(".txt"))
    detail = f"HTTP {r.status_code}  {len(r.content)}B  {first!r}"
    if reason:
        who = who_answered(r.headers)
        detail += f"  -- {reason}" + (f"  [{who}]" if who else "")
    return not reason, detail, reason


def cert_issuer(host: str = HOST) -> str:
    """Who signed the certificate we are actually shown.

    A public CA means we reached the site. A corporate or vendor name means
    something on the path is re-signing traffic, which is the single most
    common reason Python fails where a browser succeeds.
    """
    der = None
    for ctx in (ctx_no_verify(), ctx_relaxed(), ctx_default()):
        try:
            with socket.create_connection((host, PORT), timeout=TIMEOUT) as s:
                with ctx.wrap_socket(s, server_hostname=host) as tls:
                    der = tls.getpeercert(binary_form=True)
            if der:
                break
        except Exception:                           # noqa: BLE001, S112
            continue
    try:
        if not der:
            return "unknown"
        try:
            from cryptography import x509
            return x509.load_der_x509_certificate(der).issuer.rfc4514_string()
        except ImportError:
            pass
        ctx2 = ssl.create_default_context()
        with socket.create_connection((host, PORT), timeout=TIMEOUT) as sock:
            with ctx2.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert() or {}
        parts = []
        for rdn in cert.get("issuer", ()):
            for k, v in rdn:
                parts.append(f"{k}={v}")
        return ", ".join(parts) or "unknown"
    except Exception as exc:                        # noqa: BLE001
        return f"could not read ({type(exc).__name__})"


PUBLIC_CAS = ("digicert", "sectigo", "globalsign", "let's encrypt",
              "letsencrypt", "entrust", "godaddy", "amazon", "google trust",
              "baltimore", "comodo", "thawte", "geotrust", "rapidssl",
              "certum", "quovadis", "identrust", "isrg")


def looks_public_ca(issuer: str) -> bool:
    low = issuer.lower()
    return any(ca in low for ca in PUBLIC_CAS)


def proxies_in_play() -> dict[str, str]:
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
            "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy")
    found = {k: os.environ[k] for k in keys if os.environ.get(k)}
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if enabled:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
                found["WindowsProxy"] = server
        except Exception:                           # noqa: BLE001, S110
            pass
    return found


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------

def run(host: str = HOST) -> int:
    """Probe, then say what is wrong in one verdict.

    Exit code: 0 reachable, or a fix you can apply here; 1 inconclusive;
    2 a deliberate refusal, with nothing on this side to configure.
    """
    print(f"monaqasat doctor -- {host}\n")

    print("environment")
    _line("python", sys.version.split()[0])
    _line("platform", platform.platform())
    _line("openssl", ssl.OPENSSL_VERSION)
    try:
        import certifi
        _line("certifi bundle", certifi.where())
    except ImportError:
        _line("certifi bundle", "not installed")
    _line("truststore available", "yes" if ctx_system_store() else
          "no  (pip install truststore)")

    proxies = proxies_in_play()
    print("\nproxy")
    if proxies:
        for k, v in proxies.items():
            _line(k, v)
    else:
        _line("none configured", "Python will connect directly")

    print("\nconnectivity")
    ok, detail = tcp_reachable(host)
    _line("TCP :443", detail)
    if not ok:
        print("\nverdict: the port is not reachable at all. That is a network "
              "or firewall question for IT, not a TLS one.")
        return 1

    print("\nTLS handshakes (raw socket -- ignores proxy settings)")
    results: dict[str, tuple[bool, str]] = {}
    for label, ctx in (
        ("default", ctx_default()),
        ("TLS 1.2 only", ctx_tls12()),
        ("relaxed ciphers", ctx_relaxed()),
        ("no verification", ctx_no_verify()),
    ):
        ok, detail = handshake(ctx, host)
        results[label] = (ok, detail)
        _line(label, ("OK   " if ok else "fail ") + detail)

    sys_ctx = ctx_system_store()
    if sys_ctx is not None:
        ok, detail = handshake(sys_ctx, host)
        results["system store"] = (ok, detail)
        _line("system cert store", ("OK   " if ok else "fail ") + detail)

    issuer = cert_issuer(host)
    print("\ncertificate")
    _line("issued by", issuer)
    unreadable = issuer == "unknown" or issuer.startswith("could not read")
    intercepted = not unreadable and not looks_public_ca(issuer)
    _line("verdict",
          "could not determine" if unreadable else
          "INTERCEPTED -- something is re-signing traffic" if intercepted
          else "public CA -- you are talking to the real server")

    print("\nHTTP through requests (what the crawler uses)")
    http_proxy_ok, http_proxy_detail, http_proxy_reason = http_get(
        use_proxy=True, host=host)
    _line("with system proxy", ("OK   " if http_proxy_ok else "fail ")
          + http_proxy_detail)
    http_direct_ok, http_direct_detail, http_direct_reason = http_get(
        use_proxy=False, host=host)
    _line("proxy bypassed", ("OK   " if http_direct_ok else "fail ")
          + http_direct_detail)

    # ------------------------------------------------------------------
    print("\nverdict")

    if http_proxy_ok:
        print("  Everything works. Re-run the crawler; if it still fails the\n"
              "  problem is elsewhere -- send the new error.")
        return 0

    if http_direct_ok and not http_proxy_ok:
        print("  Direct works, going through the proxy does not.\n\n"
              f"  Fix: tell the crawler to bypass the proxy for this host --\n"
              f"      set NO_PROXY={host}\n"
              "  or clear HTTP_PROXY/HTTPS_PROXY in this shell. If your\n"
              "  network requires the proxy for outbound traffic, ask IT for\n"
              "  the correct proxy URL including any authentication.")
        return 0

    refusal = http_proxy_reason or http_direct_reason
    if refusal:
        # Something answered. That is not the same as the site working, and
        # the old version of this report read it as exactly that.
        print("  Something answered in the server's place and refused:\n"
              f"    with the system proxy : "
              f"{http_proxy_reason or http_proxy_detail}\n"
              f"    proxy bypassed        : "
              f"{http_direct_reason or http_direct_detail}\n")
        if "407" in http_proxy_reason + http_direct_reason:
            print("  HTTP 407 is the proxy asking for credentials. requests\n"
                  "  does not use Windows single sign-on, so it cannot supply\n"
                  "  them the way Chrome does. Ask IT either for a proxy URL\n"
                  "  that carries authentication --\n"
                  "      set HTTPS_PROXY=http://user:password@proxy:port\n"
                  f"  -- or for {host} to be allowed without it.\n")
        else:
            print(f"  Ask IT whether outbound traffic to {host} is allowed\n"
                  "  for scripts as well as browsers, and what proxy a script\n"
                  "  should use.\n")
        print("  This is a refusal, not a failure to connect. The handshakes\n"
              "  above succeeded, so nothing here is a certificate problem --\n"
              "  and a run that gets this far still fetches no pages.")
        return 2

    if intercepted:
        print(f"  The certificate is signed by:\n    {issuer}\n\n"
              "  That is not the site's own certificate -- your network is\n"
              "  inspecting TLS. Windows trusts that root, so Chrome is fine;\n"
              "  Python uses its own bundle and is not.\n\n"
              "  Fix, in order of preference:\n"
              "    1. pip install truststore      (uses the Windows store)\n"
              "    2. ask IT for the root CA file, then\n"
              "       set REQUESTS_CA_BUNDLE=C:\\path\\to\\corporate-root.crt\n\n"
              "  Do not disable verification. On a bank network that turns a\n"
              "  fixable config problem into a real one.")
        return 0

    if results.get("system store", (False, ""))[0] and not results["default"][0]:
        print("  The Windows certificate store works where Python's bundle\n"
              "  does not.\n\n  Fix:  pip install truststore   then re-run.")
        return 0

    if (results["TLS 1.2 only"][0] or results["relaxed ciphers"][0]) \
            and not results["default"][0]:
        which = "TLS 1.2" if results["TLS 1.2 only"][0] else "relaxed ciphers"
        print(f"  The server needs {which} -- an ordinary interop mismatch\n"
              "  with OpenSSL 3 defaults.\n\n"
              "  Fix:  set MONAQASAT_TLS=compat    then re-run.")
        return 0

    if not any(ok for ok, _ in results.values()):
        print("  Every handshake is closed by the server without an alert,\n"
              "  including the unverified one, while a browser on the same\n"
              "  machine loads the site.\n\n"
              "  Two possibilities, and they need different answers:\n\n"
              "  a) Your network blocks this host for non-browser processes.\n"
              "     Ask IT whether outbound traffic to monaqasat.mof.gov.qa\n"
              "     is restricted, and what proxy a script should use.\n\n"
              "  b) The server itself declines clients that are not browsers.\n"
              "     If so that is a control the operator put there, and the\n"
              "     honest routes are to ask the Ministry of Finance for the\n"
              "     data directly -- QDB is a state bank asking a ministry for\n"
              "     figures it already publishes -- or to drive a real browser.\n\n"
              "  Forging a browser TLS fingerprint would get round it. Don't:\n"
              "  it defeats a deliberate control, and it is not something a\n"
              "  bank can defend if anyone asks how the data was obtained.")
        return 2

    print("  Mixed results -- send this output and I will read it.")
    return 1
