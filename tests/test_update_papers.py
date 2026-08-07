import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.update_papers import (
    AnalysisBatch,
    PaperAnalysis,
    analyze_with_openai,
    find_new_or_updated,
    parse_atom_feed,
    score_paper,
    update_data,
)


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
    def test_gpt_analysis_batches_requests_and_sets_provider_options(self):
        papers = [
            {
                "id": f"paper-{index}",
                "title": f"Paper {index}",
                "abstract": "CMB abstract",
                "categories": ["astro-ph.CO"],
                "tags": ["CMB"],
            }
            for index in range(4)
        ]

        def analysis_for(paper_id):
            return PaperAnalysis(
                paper_id=paper_id,
                title_zh="标题",
                summary_zh="摘要",
                why_it_matters_zh="意义",
                key_points=["要点"],
                methods=["方法"],
                reading_note_zh="精读提示",
                audience="领域相关",
                novelty_score=7,
                confidence=80,
            )

        batches = [
            AnalysisBatch(analyses=[analysis_for("paper-0"), analysis_for("paper-1")]),
            AnalysisBatch(analyses=[analysis_for("paper-2"), analysis_for("paper-3")]),
        ]
        with patch("openai.OpenAI") as openai_class:
            client = openai_class.return_value
            client.responses.parse.side_effect = [
                SimpleNamespace(output_parsed=batch) for batch in batches
            ]
            result = analyze_with_openai(
                papers,
                "gpt-test",
                "test-key",
                base_url="https://provider.example/v1",
                user_agent="provider-client",
                batch_size=2,
                reasoning_effort="low",
            )

        self.assertEqual(set(result), {paper["id"] for paper in papers})
        self.assertEqual(client.responses.parse.call_count, 2)
        self.assertEqual(
            client.responses.parse.call_args_list[0].kwargs["reasoning"],
            {"effort": "low"},
        )
        openai_class.assert_called_once_with(
            api_key="test-key",
            timeout=120.0,
            max_retries=0,
            base_url="https://provider.example/v1",
            default_headers={"User-Agent": "provider-client"},
        )

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

    def test_new_paper_detection_uses_id_and_content_hash(self):
        paper = parse_atom_feed(ATOM_SAMPLE, "test")[0]
        existing = {"papers": [dict(paper)]}
        self.assertEqual(find_new_or_updated([paper], existing), [])
        revised = dict(paper, content_hash="changed")
        self.assertEqual(find_new_or_updated([revised], existing), [revised])

    def test_strict_mode_skips_before_fetch_when_key_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            output_path = Path(temp_dir) / "papers.json"
            config_path.write_text(json.dumps({"queries": [{"name": "test", "query": "all:CMB"}]}))
            output_path.write_text(json.dumps({"meta": {}, "papers": [{"id": "existing"}]}))
            args = argparse.Namespace(
                config=str(config_path),
                output=str(output_path),
                model="",
                max_results=None,
                no_ai=False,
                require_ai=True,
                skip_if_no_new=True,
                force_ai=False,
            )
            with patch.dict(os.environ, {"GPT_API_KEY": "", "OPENAI_API_KEY": ""}):
                outcome = update_data(args)
            self.assertFalse(outcome.changed)
            self.assertEqual(outcome.reason, "missing_api_key")

    def test_strict_mode_keeps_existing_data_when_gpt_rejects_key(self):
        paper = parse_atom_feed(ATOM_SAMPLE, "test")[0]
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            output_path = Path(temp_dir) / "papers.json"
            config_path.write_text(
                json.dumps(
                    {
                        "queries": [{"name": "test", "query": "all:CMB"}],
                        "focus_limit": 12,
                        "discovery_limit": 6,
                        "analysis_limit": 18,
                    }
                )
            )
            existing = {"meta": {"sentinel": "keep"}, "papers": []}
            output_path.write_text(json.dumps(existing))
            args = argparse.Namespace(
                config=str(config_path),
                output=str(output_path),
                model="",
                max_results=None,
                no_ai=False,
                require_ai=True,
                skip_if_no_new=True,
                force_ai=False,
            )
            with (
                patch.dict(os.environ, {"GPT_API_KEY": "invalid", "OPENAI_API_KEY": ""}),
                patch("scripts.update_papers.fetch_all", return_value=([paper], [])),
                patch("scripts.update_papers.analyze_with_openai", side_effect=RuntimeError("401 invalid")),
            ):
                outcome = update_data(args)
            self.assertFalse(outcome.changed)
            self.assertEqual(outcome.reason, "ai_error")
            self.assertEqual(outcome.data, existing)


if __name__ == "__main__":
    unittest.main()
