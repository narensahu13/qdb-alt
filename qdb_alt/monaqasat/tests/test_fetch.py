"""TLS negotiation and block detection. No network."""

import os
import ssl
import unittest
from unittest import mock

import requests

from monaqasat.fetch import Fetcher, ssl_candidates
from monaqasat.parse import looks_rejected


class Candidates(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("MONAQASAT_TLS", None)

    def test_strictest_first_then_step_down(self):
        names = [n for n, _ in ssl_candidates()]
        self.assertEqual(names[0], "default")
        self.assertIn("compat", names[-1])

    def test_compat_is_last_resort_not_first(self):
        names = [n for n, _ in ssl_candidates()]
        self.assertGreater(len(names), 1,
                           "compat must never be the only option tried")

    def test_env_var_pins_one(self):
        os.environ["MONAQASAT_TLS"] = "compat"
        self.assertEqual(len(ssl_candidates()), 1)
        self.assertEqual(ssl_candidates()[0][0], "compat")

    def test_compat_still_verifies_certificates(self):
        os.environ["MONAQASAT_TLS"] = "compat"
        _, ctx = ssl_candidates()[0]
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_no_candidate_ever_disables_verification(self):
        for name, ctx in ssl_candidates():
            if ctx is None:
                continue
            self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED, name)
            self.assertTrue(ctx.check_hostname, name)


class Negotiation(unittest.TestCase):
    """The real server closes the connection on strict settings and accepts
    a relaxed cipher level. The fetcher must step down on its own."""

    def _fetcher(self):
        with mock.patch.object(Fetcher, "_new_session"):
            f = Fetcher(delay=0)
        f._candidates = [("default", None), ("compat", None)]
        return f

    def test_steps_down_past_a_tls_error(self):
        f = self._fetcher()
        ok = mock.Mock(status_code=200, text="User-agent: *\nDisallow:")
        ok.raise_for_status = mock.Mock()
        calls = []

        def fake_new_session(candidate):
            calls.append(candidate[0])
            f.session = mock.Mock()
            f.tls_mode = candidate[0]
            f.session.get.side_effect = (
                [requests.exceptions.SSLError("EOF")] if candidate[0] == "default"
                else [ok])

        with mock.patch.object(f, "_new_session", side_effect=fake_new_session):
            text = f.negotiate()

        self.assertEqual(calls, ["default", "compat"])
        self.assertEqual(f.tls_mode, "compat")
        self.assertIn("User-agent", text)

    def test_keeps_the_first_setting_that_works(self):
        f = self._fetcher()
        ok = mock.Mock(status_code=200, text="User-agent: *")
        ok.raise_for_status = mock.Mock()
        calls = []

        def fake_new_session(candidate):
            calls.append(candidate[0])
            f.session = mock.Mock()
            f.tls_mode = candidate[0]
            f.session.get.return_value = ok

        with mock.patch.object(f, "_new_session", side_effect=fake_new_session):
            f.negotiate()

        self.assertEqual(calls, ["default"], "must not try compat needlessly")

    def test_a_proxy_error_is_not_retried_with_ciphers(self):
        f = self._fetcher()
        calls = []

        def fake_new_session(candidate):
            calls.append(candidate[0])
            f.session = mock.Mock()
            f.session.get.side_effect = requests.exceptions.ProxyError("nope")

        with mock.patch.object(f, "_new_session", side_effect=fake_new_session):
            with self.assertRaises(ConnectionError) as ctx:
                f.negotiate()

        self.assertEqual(calls, ["default"])
        self.assertIn("proxy", str(ctx.exception).lower())

    def test_all_failing_collects_every_attempt(self):
        f = self._fetcher()

        def fake_new_session(candidate):
            f.session = mock.Mock()
            f.session.get.side_effect = requests.exceptions.SSLError("EOF")

        with mock.patch.object(f, "_new_session", side_effect=fake_new_session):
            with self.assertRaises(ConnectionError) as ctx:
                f.negotiate()

        msg = str(ctx.exception)
        self.assertIn("default", msg)
        self.assertIn("compat", msg)
        self.assertIn("doctor", msg)


class FirewallPage(unittest.TestCase):
    def test_a_200_with_a_rejection_body_is_not_success(self):
        self.assertTrue(looks_rejected(
            "<html>The requested URL was rejected. "
            "Your support ID is: 999</html>"))

    def test_ordinary_html_passes(self):
        self.assertFalse(looks_rejected("<html><body>Awarded</body></html>"))


class EnglishCookie(unittest.TestCase):
    def test_session_asks_for_english(self):
        f = Fetcher(delay=0)
        self.assertEqual(
            f.session.cookies.get(".AspNetCore.Culture"),
            "c%3Den%7Cuic%3Den")



class Resilience(unittest.TestCase):
    """The ReadTimeout on register page 2 (Sept 2026)."""

    def test_default_timeout_covers_the_slowest_page_seen(self):
        self.assertGreaterEqual(Fetcher(delay=0).read_timeout, 120)

    def test_a_timeout_gets_a_fresh_connection_before_the_retry(self):
        f = Fetcher(delay=0, jitter=0)
        first = f.session
        ok = requests.Response()
        ok.status_code = 200
        ok._content = b"<html>fine</html>"
        ok.headers["Content-Type"] = "text/html; charset=utf-8"
        with mock.patch.object(requests.Session, "get",
                               side_effect=[requests.exceptions.ReadTimeout("slow"),
                                            ok]), \
                mock.patch("time.sleep"):
            text, status = f.get("/ClassifiedCompaniesProfilesList/2")
        self.assertEqual((text, status), ("<html>fine</html>", 200))
        self.assertIsNot(f.session, first, "the retry must not reuse the socket")
        self.assertEqual(f.session.cookies.get(".AspNetCore.Culture"),
                         "c%3Den%7Cuic%3Den", "the new session still asks for English")


class Decoding(unittest.TestCase):
    def _resp(self, body: bytes, ctype: str):
        r = requests.Response()
        r.status_code = 200
        r._content = body
        r.headers["Content-Type"] = ctype
        return r

    def test_arabic_without_a_declared_charset_is_utf8(self):
        from monaqasat.fetch import decode_body
        body = "قطر للوقود".encode("utf-8")
        self.assertEqual(decode_body(self._resp(body, "text/html")), "قطر للوقود")

    def test_a_declared_charset_is_respected(self):
        from monaqasat.fetch import decode_body
        body = "café".encode("latin-1")
        self.assertEqual(
            decode_body(self._resp(body, "text/html; charset=ISO-8859-1")),
            "café")


if __name__ == "__main__":
    unittest.main()
