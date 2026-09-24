"""`doctor` must not call a block page a working connection.

A blocked request still answers. The WAF returns its own page with HTTP 200,
a corporate proxy returns 407 or an HTML interstitial, and `requests` raises
nothing at all -- so the old report treated "we got a response" as success
and printed "Everything works" on a network where the crawler could not
fetch a single page. That is the worst kind of wrong: it sends you looking
at the parser when the traffic never left the building.

The other half of these tests guards the over-correction: a genuine
robots.txt must still read as healthy.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

try:
    from monaqasat import diagnose
except ImportError:                                    # run from tests/
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from monaqasat import diagnose


WAF_PAGE = ("<html><head><title>Request Rejected</title></head><body>"
            "The requested URL was rejected. Please consult with your "
            "administrator.<br><br>Your support ID is: 8172635401"
            "</body></html>")

ROBOTS = "User-agent: *\nDisallow: /TendersOnlineServices/Download\n"


class Resp:
    """The parts of a requests.Response this module reads."""

    def __init__(self, status=200, text="", headers=None):
        self.status_code = status
        self.text = text
        self.content = text.encode()
        self.headers = headers or {}


class WhatCountsAsAnAnswer(unittest.TestCase):
    def test_the_wafs_own_page_is_not_a_successful_fetch(self):
        # HTTP 200, a body, no exception -- and nothing we asked for.
        reason = diagnose.block_reason(200, {"Content-Type": "text/html"},
                                       WAF_PAGE)
        self.assertTrue(reason)
        self.assertIn("block page", reason)

    def test_proxy_authentication_is_named_not_just_numbered(self):
        reason = diagnose.block_reason(407, {}, "")
        self.assertIn("407", reason)
        self.assertIn("authentication", reason)

    def test_a_forbidden_status_is_a_refusal(self):
        self.assertTrue(diagnose.block_reason(403, {}, ""))

    def test_an_interstitial_served_as_200_is_caught_by_its_shape(self):
        # No recognised block-page wording, just HTML where robots.txt
        # should be plain text. Still not the server.
        reason = diagnose.block_reason(
            200, {"Content-Type": "text/html; charset=utf-8"},
            "<html><body>Access to this site requires sign-in</body></html>")
        self.assertTrue(reason)
        self.assertIn("HTML", reason)

    def test_the_html_rule_only_applies_where_text_was_expected(self):
        # A tender page is HTML and that is correct; the rule is about
        # robots.txt, not about HTML being suspicious in itself.
        self.assertEqual(
            diagnose.block_reason(200, {"Content-Type": "text/html"},
                                  "<html><body>a tender</body></html>",
                                  expect_text=False),
            "")

    def test_a_real_robots_file_is_left_alone(self):
        self.assertEqual(
            diagnose.block_reason(200, {"Content-Type": "text/plain"},
                                  ROBOTS),
            "")

    def test_the_box_that_refused_is_named_for_it(self):
        self.assertIn("proxy.qdb.local",
                      diagnose.who_answered({"Via": "1.1 proxy.qdb.local"}))


class TheHttpProbe(unittest.TestCase):
    def _get(self, resp, **kw):
        with mock.patch("requests.get", return_value=resp) as g:
            out = diagnose.http_get(use_proxy=True, **kw)
        return out, g

    def test_a_block_page_reports_failure_and_says_why(self):
        (ok, detail, reason), _ = self._get(
            Resp(200, WAF_PAGE, {"Content-Type": "text/html",
                                 "Via": "1.1 waf01"}))
        self.assertFalse(ok)
        self.assertTrue(reason)
        self.assertIn("block page", detail)
        self.assertIn("waf01", detail)          # who to ask IT about

    def test_a_407_reports_failure(self):
        (ok, _, reason), _ = self._get(Resp(407, "", {}))
        self.assertFalse(ok)
        self.assertIn("407", reason)

    def test_a_real_answer_reports_success(self):
        (ok, detail, reason), _ = self._get(
            Resp(200, ROBOTS, {"Content-Type": "text/plain"}))
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertIn("HTTP 200", detail)

    def test_a_transport_error_is_still_a_failure_with_no_block_reason(self):
        with mock.patch("requests.get", side_effect=OSError("no route")):
            ok, detail, reason = diagnose.http_get(use_proxy=False)
        self.assertFalse(ok)
        self.assertEqual(reason, "")            # nothing answered, so nobody
        self.assertIn("OSError", detail)        # refused; that is different

    def test_the_host_argument_is_the_host_probed(self):
        # --host used to reach the socket probes but not this one, so a
        # second site was diagnosed by asking the first.
        _, g = self._get(Resp(200, ROBOTS, {"Content-Type": "text/plain"}),
                         host="icv.tawteen.com.qa")
        self.assertIn("icv.tawteen.com.qa", g.call_args[0][0])


class TheVerdict(unittest.TestCase):
    """run() with every probe stubbed: TLS fine, HTTP refused."""

    def _run(self, proxy, direct, host="monaqasat.mof.gov.qa"):
        answers = iter((proxy, direct))
        with mock.patch.object(diagnose, "tcp_reachable",
                               return_value=(True, "connected")), \
             mock.patch.object(diagnose, "handshake",
                               return_value=(True, "TLSv1.3 AES")), \
             mock.patch.object(diagnose, "ctx_system_store",
                               return_value=None), \
             mock.patch.object(diagnose, "cert_issuer",
                               return_value="O=DigiCert Inc"), \
             mock.patch.object(diagnose, "http_get",
                               side_effect=lambda *a, **k: next(answers)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = diagnose.run(host)
        return code, buf.getvalue()

    BLOCKED = (False, "HTTP 200  512B  '<html>'  -- the firewall's block "
                      "page, served as HTTP 200",
               "the firewall's block page, served as HTTP 200")
    PROXY_AUTH = (False, "HTTP 407  0B  ''  -- HTTP 407 -- the proxy "
                         "requires authentication",
                  "HTTP 407 -- the proxy requires authentication")
    FINE = (True, "HTTP 200  84B  'User-agent: *'", "")

    def test_a_block_page_is_never_reported_as_working(self):
        code, out = self._run(self.BLOCKED, self.BLOCKED)
        self.assertEqual(code, 2)
        self.assertNotIn("Everything works", out)
        self.assertIn("refused", out)

    def test_the_verdict_says_it_is_a_refusal_not_a_connection_failure(self):
        _, out = self._run(self.BLOCKED, self.BLOCKED)
        self.assertIn("not a failure to connect", out)
        self.assertIn("fetches no pages", out)

    def test_proxy_authentication_gets_the_fix_that_applies_to_it(self):
        code, out = self._run(self.PROXY_AUTH, self.PROXY_AUTH)
        self.assertEqual(code, 2)
        self.assertIn("HTTPS_PROXY", out)
        self.assertIn("single sign-on", out)

    def test_a_working_site_still_reads_as_working(self):
        # The guard against over-correcting into a permanent alarm.
        code, out = self._run(self.FINE, self.FINE)
        self.assertEqual(code, 0)
        self.assertIn("Everything works", out)

    def test_direct_working_still_points_at_the_proxy(self):
        code, out = self._run(self.BLOCKED, self.FINE)
        self.assertEqual(code, 0)
        self.assertIn("NO_PROXY", out)

    def test_the_bypass_advice_names_the_host_asked_about(self):
        _, out = self._run(self.BLOCKED, self.FINE, host="icv.tawteen.com.qa")
        self.assertIn("NO_PROXY=icv.tawteen.com.qa", out)


if __name__ == "__main__":
    unittest.main()
