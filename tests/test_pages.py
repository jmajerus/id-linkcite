import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from pages import Match


class MatchTests(unittest.TestCase):
    def test_name_and_site_from_address(self):
        m = Match("https://www.deceptive.design/types/hard-to-cancel/")
        self.assertEqual((m.name, m.site), ("Hard to cancel", "deceptive.design"))
        self.assertEqual(Match("https://example.org/x/fake-urgency").name, "Fake urgency")


if __name__ == "__main__":
    unittest.main()
