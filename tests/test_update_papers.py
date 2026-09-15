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
    ArxivRateLimitError,
    PaperAnalysis,
    add_submitted_date_window,
    analyze_with_openai,
    api_mode_candidates,
    find_new_or_updated,
    fetch_all,
    model_candidates,
    parse_atom_feed,
    parse_rss_feed,
    score_paper,
    select_archive,
    select_current,
    select_daily_analysis_candidates,
    select_daily_archive,
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

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <item>
      <title>A complete RSS fallback paper</title>
      <link>https://arxiv.org/abs/2609.00001</link>
      <description>arXiv:2609.00001v2 Announce Type: replace Abstract: We update a CMB analysis.</description>
      <guid>oai:arXiv.org:2609.00001v2</guid>
      <category>astro-ph.CO</category>
      <category>gr-qc</category>
      <pubDate>Tue, 15 Sep 2026 00:00:00 -0400</pubDate>
      <arxiv:announce_type>replace</arxiv:announce_type>
      <dc:creator>A. Researcher, B. Scientist</dc:creator>
    </item>
  </channel>
</rss>
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
                max_retries=3,
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
            max_retries=3,
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

    def test_rss_parsing_provides_complete_analysis_metadata(self):
        papers = parse_rss_feed(RSS_SAMPLE, "astro-ph.CO-rss")
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper["id"], "2609.00001")
        self.assertEqual(paper["versioned_id"], "2609.00001v2")
        self.assertEqual(paper["abstract"], "We update a CMB analysis.")
        self.assertEqual(paper["authors"], ["A. Researcher", "B. Scientist"])
        self.assertEqual(paper["announce_type"], "replace")
        self.assertEqual(paper["published"], "2026-09-15T04:00:00Z")

    def test_arxiv_429_uses_rss_and_stops_search_queries(self):
        config = {
            "complete_category": "astro-ph.CO",
            "queries": [
                {"name": "co", "query": "cat:astro-ph.CO", "max_results": 5},
                {"name": "other", "query": "cat:gr-qc", "max_results": 5},
            ],
        }
        with (
            patch("scripts.update_papers.request_feed", side_effect=ArxivRateLimitError("429")) as api,
            patch("scripts.update_papers.request_rss", return_value=RSS_SAMPLE) as rss,
        ):
            papers, errors = fetch_all(config)
        self.assertEqual([paper["id"] for paper in papers], ["2609.00001"])
        self.assertEqual(api.call_count, 1)
        rss.assert_called_once()
        self.assertIn("429", errors[0])

    def test_model_candidates_are_ordered_and_deduplicated(self):
        self.assertEqual(
            model_candidates("gpt-main", "gpt-backup, gpt-main, gpt-small"),
            ["gpt-main", "gpt-backup", "gpt-small"],
        )

    def test_api_mode_candidates_are_ordered_and_deduplicated(self):
        self.assertEqual(
            api_mode_candidates("responses", "chat_completions,responses"),
            ["responses", "chat_completions"],
        )

    def test_cmb_paper_scores_as_focus(self):
        paper = parse_atom_feed(ATOM_SAMPLE, "test")[0]
        scored = score_paper(paper, datetime(2026, 8, 7, tzinfo=timezone.utc))
        self.assertEqual(scored["track"], "focus")
        self.assertGreaterEqual(scored["scores"]["cmb"], 30)
        self.assertIn("CMB", scored["tags"])
        self.assertIn("cmb-core", scored["topics"])
        self.assertIn("polarization", scored["topics"])

    def test_cmb_s4_is_tagged_as_an_observing_project(self):
        paper = parse_atom_feed(ATOM_SAMPLE, "test")[0]
        paper["title"] = "Sensitivity of Next-Generation CMB Surveys to Light Relics"
        paper["abstract"] = "CMB-S4 forecasts constraints on neutrinos and light relics."
        scored = score_paper(paper, datetime(2026, 8, 7, tzinfo=timezone.utc))
        self.assertEqual(scored["track"], "focus")
        self.assertIn("观测项目", scored["tags"])

    def test_current_selection_prioritizes_new_release_date(self):
        candidates = [
            {
                "id": f"older-{index}",
                "published": f"2026-08-06T{index:02d}:00:00Z",
                "track": "focus",
                "scores": {"editorial": 100 - index, "interest": 100 - index},
            }
            for index in range(13)
        ]
        candidates.append(
            {
                "id": "new-cmb-paper",
                "published": "2026-08-07T17:46:01Z",
                "track": "focus",
                "scores": {"editorial": 25, "interest": 25},
            }
        )
        _, focus_ids, _ = select_current(
            candidates,
            {"lookback_days": 21, "focus_limit": 12, "discovery_limit": 0},
            datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        self.assertEqual(focus_ids[0], "new-cmb-paper")

    def test_daily_archive_keeps_every_target_category_paper(self):
        candidates = [
            {
                "id": f"co-{index}",
                "published": f"2026-08-07T{index:02d}:00:00Z",
                "categories": ["astro-ph.CO"],
            }
            for index in range(25)
        ]
        adjacent = {
            "id": "interesting-gravity",
            "published": "2026-08-07T23:00:00Z",
            "categories": ["gr-qc"],
        }
        selected = select_daily_archive(
            candidates + [adjacent],
            [candidates[0], adjacent],
            "astro-ph.CO",
        )
        self.assertEqual(len(selected), 26)
        self.assertTrue({paper["id"] for paper in candidates}.issubset({paper["id"] for paper in selected}))

    def test_daily_analysis_includes_all_new_complete_category_papers(self):
        papers = [
            {
                "id": f"co-{index}",
                "published": f"2026-08-11T{index:02d}:00:00Z",
                "updated": f"2026-08-11T{index:02d}:00:00Z",
                "categories": ["astro-ph.CO"],
                "analysis": {"provider": "fallback"},
            }
            for index in range(25)
        ]
        candidates = select_daily_analysis_candidates(
            papers,
            papers,
            papers[:1],
            "astro-ph.CO",
        )
        self.assertEqual({paper["id"] for paper in candidates}, {paper["id"] for paper in papers})

    def test_force_ai_fills_latest_pending_papers_before_reanalysis(self):
        papers = [
            {
                "id": "older-pending",
                "first_selected_at": "2026-08-10T00:00:00Z",
                "updated": "2026-08-10T00:00:00Z",
                "published": "2026-08-10T00:00:00Z",
                "categories": ["astro-ph.CO"],
                "analysis": {"provider": "fallback"},
            },
            {
                "id": "latest-pending",
                "first_selected_at": "2026-08-11T00:00:00Z",
                "updated": "2026-08-11T00:00:00Z",
                "published": "2026-08-11T00:00:00Z",
                "categories": ["astro-ph.CO"],
                "analysis": {"provider": "fallback"},
            },
            {
                "id": "already-analyzed",
                "first_selected_at": "2026-08-11T01:00:00Z",
                "updated": "2026-08-11T01:00:00Z",
                "published": "2026-08-11T01:00:00Z",
                "categories": ["astro-ph.CO"],
                "analysis": {"provider": "openai"},
            },
        ]
        candidates = select_daily_analysis_candidates(
            papers,
            [],
            [papers[2]],
            "astro-ph.CO",
            force_ai=True,
        )
        self.assertEqual([paper["id"] for paper in candidates], ["latest-pending", "older-pending"])

    def test_archive_keeps_complete_category_beyond_monthly_limits(self):
        now = datetime(2026, 8, 10, tzinfo=timezone.utc)
        candidates = [
            {
                "id": f"co-{index}",
                "published": f"2026-08-{index + 1:02d}T00:00:00Z",
                "updated": f"2026-08-{index + 1:02d}T00:00:00Z",
                "categories": ["astro-ph.CO"],
                "track": "discovery",
                "scores": {"editorial": 1, "interest": 1},
            }
            for index in range(5)
        ]
        selected = select_archive(
            candidates,
            {
                "archive_focus_per_month": 0,
                "archive_discovery_per_month": 1,
                "complete_category": "astro-ph.CO",
            },
            now,
            90,
        )
        self.assertEqual({paper["id"] for paper in selected}, {paper["id"] for paper in candidates})

    def test_submitted_date_window_and_monthly_archive_balance(self):
        now = datetime(2026, 8, 7, tzinfo=timezone.utc)
        query = add_submitted_date_window("cat:astro-ph.CO", 90, now)
        self.assertIn("submittedDate:[202605090000 TO 202608070000]", query)

        candidates = []
        for month, score in (("2026-08", 90), ("2026-07", 80)):
            for index in range(2):
                candidates.append(
                    {
                        "id": f"{month}-{index}",
                        "published": f"{month}-0{index + 1}T00:00:00Z",
                        "track": "focus",
                        "scores": {"editorial": score - index, "interest": 20},
                    }
                )
        selected = select_archive(
            candidates,
            {"archive_focus_per_month": 1, "archive_discovery_per_month": 0},
            now,
            90,
        )
        self.assertEqual({paper["id"] for paper in selected}, {"2026-08-0", "2026-07-0"})

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
