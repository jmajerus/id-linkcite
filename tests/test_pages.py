import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from rdflib import Literal, URIRef

from pages import Match


class MatchTests(unittest.TestCase):
    def test_name_and_site_from_address(self):
        m = Match("https://www.deceptive.design/types/hard-to-cancel/")
        self.assertEqual((m.name, m.site), ("Hard to cancel", "deceptive.design"))
        self.assertEqual(Match("https://example.org/x/fake-urgency").name, "Fake urgency")

    def test_only_web_addresses_with_a_host_are_linkable(self):
        self.assertTrue(Match.linkable(URIRef("https://www.deceptive.design/types/confirmshaming/")))
        for unsafe in ("javascript:alert(1)", "data:text/html,x", "urn:isbn:123", "https:///no-host", "ftp://x.org/a"):
            self.assertFalse(Match.linkable(URIRef(unsafe)), unsafe)
        self.assertFalse(Match.linkable(Literal("https://example.org/")))


if __name__ == "__main__":
    unittest.main()
