from datetime import datetime, timezone
import unittest

from scripts.update_papers import parse_atom_feed, score_paper


ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2608.00001v1</id>
    <updated>2026-08-05T00:00:00Z</updated>
    <published>2026-08-05T00:00:00Z</published>
    <title>A new CMB B-mode analysis</title>
    <summary>We present a cosmic microwave background B-mode component separation analysis.</summary>
    <author><name>A. Researcher</name></author>
    <link href="http://arxiv.org/abs/2608.00001v1" rel="alternate" type="text/html" />
    <link title="pdf" href="http://arxiv.org/pdf/2608.00001v1" rel="related" type="application/pdf" />
    <arxiv:primary_category term="astro-ph.CO" />
    <category term="astro-ph.CO" />
  </entry>
</feed>
"""


class UpdatePapersTests(unittest.TestCase):
    def test_atom_parsing_normalizes_identifier_and_links(self):
        papers = parse_atom_feed(ATOM_SAMPLE, "test")
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper["id"], "2608.00001")
        self.assertEqual(paper["primary_category"], "astro-ph.CO")
        self.assertTrue(paper["pdf_url"].startswith("https://"))

    def test_cmb_paper_scores_as_focus(self):
        paper = parse_atom_feed(ATOM_SAMPLE, "test")[0]
        scored = score_paper(paper, datetime(2026, 8, 7, tzinfo=timezone.utc))
        self.assertEqual(scored["track"], "focus")
        self.assertGreaterEqual(scored["scores"]["cmb"], 30)
        self.assertIn("CMB", scored["tags"])


if __name__ == "__main__":
    unittest.main()
