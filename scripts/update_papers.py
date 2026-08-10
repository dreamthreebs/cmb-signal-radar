#!/usr/bin/env python3
"""Fetch recent arXiv papers, rank them, and optionally add OpenAI analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from pydantic import BaseModel, Field


ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
DEFAULT_MODEL = "gpt-5.6"
LOGGER = logging.getLogger("cmb-radar")


@dataclass(frozen=True)
class WeightedTerm:
    label: str
    patterns: tuple[str, ...]
    weight: int


@dataclass(frozen=True)
class UpdateOutcome:
    data: dict[str, Any]
    changed: bool
    reason: str
    new_count: int = 0


CMB_TERMS = (
    WeightedTerm("CMB", ("cosmic microwave background", r"\bcmb\b"), 18),
    WeightedTerm("B 模偏振", ("b-mode", "b mode", "b-modes", "b modes"), 12),
    WeightedTerm("CMB 透镜", ("cmb lensing", "lensing reconstruction"), 11),
    WeightedTerm("原初引力波", ("primordial gravitational wave", "tensor-to-scalar"), 10),
    WeightedTerm("再电离", ("reionization", "optical depth"), 8),
    WeightedTerm("前景去除", ("foreground", "component separation"), 7),
    WeightedTerm("次级各向异性", ("sunyaev", "kinetic sz", "thermal sz", "cmb anisotrop"), 6),
    WeightedTerm("暴胀", ("inflation", "primordial power spectrum"), 6),
    WeightedTerm("观测项目", ("simons observatory", "litebird", "cmb-s4", "spt-3g", "act dr", "planck"), 7),
)

INTEREST_TERMS = (
    WeightedTerm("新探测", ("first detection", "evidence for", "discovery", "detected"), 13),
    WeightedTerm("张力与反常", ("tension", "anomaly", "unexpected", "excess"), 11),
    WeightedTerm("新约束", ("new constraint", "tightest constraint", "unprecedented", "percent-level"), 10),
    WeightedTerm("暗能量", ("dark energy", "hubble constant", "hubble tension"), 8),
    WeightedTerm("暗物质", ("dark matter", "axion", "primordial black hole"), 8),
    WeightedTerm("引力波", ("gravitational wave", "gravitational-wave"), 8),
    WeightedTerm("黑洞", ("black hole", "event horizon"), 7),
    WeightedTerm("中微子", ("neutrino",), 7),
    WeightedTerm("AI 方法", ("machine learning", "neural network", "foundation model"), 6),
    WeightedTerm("大型巡天", ("desi", "euclid", "jwst", "rubin", "roman space telescope"), 6),
)

CATEGORY_TAGS = {
    "astro-ph.CO": "宇宙学",
    "astro-ph.IM": "仪器与方法",
    "astro-ph.GA": "星系天体物理",
    "astro-ph.HE": "高能天体物理",
    "gr-qc": "引力与相对论",
    "hep-ph": "粒子宇宙学",
    "hep-th": "高能理论",
}

TOPIC_RULES: dict[str, tuple[str, ...]] = {
    "polarization": (
        r"\bpolarization\b",
        r"\bpolarisation\b",
        r"\bb[- ]?modes?\b",
        r"\be[- ]?modes?\b",
        "tensor-to-scalar",
    ),
    "lensing-lss": (
        "cmb lensing",
        "gravitational lensing",
        "lensing reconstruction",
        "large-scale structure",
        r"\blss\b",
        "galaxy clustering",
    ),
    "early-universe": (
        r"\binflation\b",
        "primordial power spectrum",
        "primordial gravitational wave",
        "early universe",
        "reionization",
        "non-gaussianity",
    ),
    "instruments": (
        "simons observatory",
        "litebird",
        "spt-3g",
        "bicep",
        "cmb-s4",
        "telescope",
        "detector",
        "instrument",
        "calibration",
    ),
    "foregrounds-methods": (
        "foreground",
        "component separation",
        "map-making",
        "power spectrum estimation",
        "likelihood",
        "simulation",
    ),
    "dark-sector": (
        "dark matter",
        "dark energy",
        "axion",
        "primordial black hole",
        "hubble constant",
        "hubble tension",
    ),
    "gravity": (
        "gravitational wave",
        "gravitational-wave",
        "black hole",
        "modified gravity",
        "general relativity",
    ),
    "surveys": (
        r"\bdesi\b",
        r"\beuclid\b",
        r"\bjwst\b",
        r"\brubin\b",
        "roman space telescope",
        "large survey",
    ),
    "ai-computation": (
        "machine learning",
        "neural network",
        "deep learning",
        "foundation model",
        "emulator",
        "simulation-based inference",
    ),
}


class PaperAnalysis(BaseModel):
    paper_id: str
    title_zh: str
    summary_zh: str
    why_it_matters_zh: str
    key_points: list[str]
    methods: list[str]
    reading_note_zh: str
    audience: str
    novelty_score: int = Field(ge=1, le=10)
    confidence: int = Field(ge=1, le=100)


class AnalysisBatch(BaseModel):
    analyses: list[PaperAnalysis]


def compact_whitespace(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def arxiv_id_from_url(url: str) -> str:
    identifier = url.rstrip("/").split("/")[-1]
    return re.sub(r"v\d+$", "", identifier)


def content_hash(title: str, abstract: str) -> str:
    payload = f"{title}\n{abstract}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _text(parent: ET.Element, name: str) -> str:
    node = parent.find(f"{ATOM}{name}")
    return compact_whitespace(node.text if node is not None else "")


def parse_atom_feed(xml_text: str, source_name: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    papers: list[dict[str, Any]] = []
    for entry in root.findall(f"{ATOM}entry"):
        entry_url = _text(entry, "id")
        identifier = arxiv_id_from_url(entry_url)
        title = _text(entry, "title")
        abstract = _text(entry, "summary")
        links = {
            link.attrib.get("title") or link.attrib.get("rel", ""): link.attrib.get("href", "")
            for link in entry.findall(f"{ATOM}link")
        }
        categories = [
            node.attrib.get("term", "") for node in entry.findall(f"{ATOM}category") if node.attrib.get("term")
        ]
        primary = entry.find(f"{ARXIV}primary_category")
        primary_category = primary.attrib.get("term", "") if primary is not None else (categories[0] if categories else "")
        authors = [_text(node, "name") for node in entry.findall(f"{ATOM}author")]
        comment = entry.find(f"{ARXIV}comment")
        journal_ref = entry.find(f"{ARXIV}journal_ref")
        doi = entry.find(f"{ARXIV}doi")
        papers.append(
            {
                "id": identifier,
                "versioned_id": entry_url.rstrip("/").split("/")[-1],
                "title": title,
                "authors": [name for name in authors if name],
                "abstract": abstract,
                "published": _text(entry, "published"),
                "updated": _text(entry, "updated"),
                "abs_url": entry_url.replace("http://", "https://"),
                "pdf_url": (links.get("pdf") or f"https://arxiv.org/pdf/{identifier}").replace("http://", "https://"),
                "categories": categories,
                "primary_category": primary_category,
                "comment": compact_whitespace(comment.text if comment is not None else ""),
                "journal_ref": compact_whitespace(journal_ref.text if journal_ref is not None else ""),
                "doi": compact_whitespace(doi.text if doi is not None else ""),
                "source_groups": [source_name],
                "content_hash": content_hash(title, abstract),
            }
        )
    return papers


def request_feed(
    session: requests.Session,
    query: str,
    max_results: int,
    retries: int = 3,
) -> str:
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    for attempt in range(retries):
        try:
            response = session.get(ARXIV_API_URL, params=params, timeout=75)
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.HTTPError(f"arXiv returned {response.status_code}", response=response)
            response.raise_for_status()
            return response.text
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            delay = 4 * (attempt + 1)
            LOGGER.warning("arXiv request failed; retrying in %ss", delay)
            time.sleep(delay)
    raise RuntimeError("unreachable")


def add_submitted_date_window(query: str, days: int, now: datetime) -> str:
    start = (now - timedelta(days=days)).strftime("%Y%m%d%H%M")
    end = now.strftime("%Y%m%d%H%M")
    return f"({query}) AND submittedDate:[{start} TO {end}]"


def fetch_all(
    config: dict[str, Any],
    max_results_override: int | None = None,
    backfill_days: int = 0,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    contact = os.getenv("ARXIV_CONTACT_EMAIL", "").strip()
    user_agent = "cmb-signal-radar/1.0"
    if contact:
        user_agent += f" (contact: {contact})"
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Accept": "application/atom+xml"})

    merged: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    queries = config.get("backfill_queries", []) if backfill_days else config.get("queries", [])
    if not queries:
        queries = config.get("queries", [])
    query_now = now or datetime.now(timezone.utc)
    for index, query_config in enumerate(queries):
        if index:
            time.sleep(3)
        name = query_config["name"]
        max_results = max_results_override or int(query_config.get("max_results", 50))
        LOGGER.info("Fetching %s (%s results max)", name, max_results)
        try:
            query = query_config["query"]
            if backfill_days:
                query = add_submitted_date_window(query, backfill_days, query_now)
            xml_text = request_feed(session, query, max_results)
            parsed = parse_atom_feed(xml_text, name)
        except (requests.RequestException, ET.ParseError) as exc:
            message = f"{name}: {exc}"
            LOGGER.error("Fetch failed: %s", message)
            errors.append(message)
            continue
        for paper in parsed:
            existing = merged.get(paper["id"])
            if existing is None:
                merged[paper["id"]] = paper
                continue
            existing["source_groups"] = sorted(set(existing["source_groups"] + paper["source_groups"]))
            existing["categories"] = sorted(set(existing["categories"] + paper["categories"]))
            if paper.get("updated", "") > existing.get("updated", ""):
                retained_groups = existing["source_groups"]
                retained_categories = existing["categories"]
                existing.update(paper)
                existing["source_groups"] = retained_groups
                existing["categories"] = retained_categories
    return list(merged.values()), errors


def matched_terms(text: str, terms: Iterable[WeightedTerm]) -> list[WeightedTerm]:
    matches: list[WeightedTerm] = []
    for term in terms:
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in term.patterns):
            matches.append(term)
    return matches


def classify_topics(paper: dict[str, Any], cmb_score: int) -> list[str]:
    searchable = f"{paper.get('title', '')} {paper.get('abstract', '')}"
    topics: list[str] = []
    if cmb_score >= 18:
        topics.append("cmb-core")
    for topic, patterns in TOPIC_RULES.items():
        if any(re.search(pattern, searchable, re.IGNORECASE) for pattern in patterns):
            topics.append(topic)
    if paper.get("primary_category") == "astro-ph.IM" and "instruments" not in topics:
        topics.append("instruments")
    if not topics:
        topics.append("cosmic-discovery")
    return topics


def score_paper(paper: dict[str, Any], now: datetime) -> dict[str, Any]:
    searchable = f"{paper['title']} {paper['abstract']}"
    cmb_matches = matched_terms(searchable, CMB_TERMS)
    interest_matches = matched_terms(searchable, INTEREST_TERMS)
    published = parse_datetime(paper["published"])
    age_days = max(0.0, (now - published).total_seconds() / 86400)
    freshness = max(0, round(22 - age_days * 1.4))
    cmb_score = min(100, sum(item.weight for item in cmb_matches))
    interest_score = min(100, freshness + sum(item.weight for item in interest_matches))

    tags: list[str] = []
    for item in cmb_matches + interest_matches:
        if item.label not in tags:
            tags.append(item.label)
    for category in paper.get("categories", []):
        label = CATEGORY_TAGS.get(category)
        if label and label not in tags:
            tags.append(label)
    paper["tags"] = tags[:6]
    paper["scores"] = {
        "cmb": cmb_score,
        "interest": interest_score,
        "editorial": min(100, round(cmb_score * 0.62 + interest_score * 0.38)),
    }
    paper["track"] = "focus" if cmb_score >= 18 else "discovery"
    paper["topics"] = classify_topics(paper, cmb_score)
    return paper


def fallback_analysis(paper: dict[str, Any]) -> dict[str, Any]:
    first_sentence = re.split(r"(?<=[.!?])\s+", paper.get("abstract", ""), maxsplit=1)[0]
    tags = paper.get("tags", [])[:3]
    topic_text = "、".join(tags) if tags else "宇宙学与天体物理"
    return {
        "provider": "fallback",
        "model": None,
        "basis": "abstract",
        "generated_at": iso_now(),
        "title_zh": "",
        "summary_zh": "AI 解读尚未启用。摘要首句：" + first_sentence,
        "why_it_matters_zh": f"这项工作涉及{topic_text}；建议结合原文确认结论、假设与统计显著性。",
        "key_points": [],
        "methods": [],
        "reading_note_zh": "当前仅展示 arXiv 元数据与规则评分。配置 GPT_API_KEY 后会自动补全中文解读。",
        "audience": "按需浏览",
        "novelty_score": max(1, min(10, round(paper["scores"]["interest"] / 10))),
        "confidence": 30,
    }


def gpt_settings(args: argparse.Namespace) -> dict[str, str]:
    return {
        "api_key": (os.getenv("GPT_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip(),
        "base_url": (os.getenv("GPT_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").strip(),
        "model": (
            args.model
            or os.getenv("GPT_MODEL")
            or os.getenv("OPENAI_MODEL")
            or DEFAULT_MODEL
        ).strip(),
        "api_mode": (os.getenv("GPT_API_MODE") or "responses").strip().lower(),
        "user_agent": (os.getenv("GPT_USER_AGENT") or "").strip(),
        "batch_size": (os.getenv("GPT_BATCH_SIZE") or "3").strip(),
        "max_retries": (os.getenv("GPT_MAX_RETRIES") or "3").strip(),
        "reasoning_effort": (os.getenv("GPT_REASONING_EFFORT") or "").strip().lower(),
    }


def analyze_with_openai(
    papers: list[dict[str, Any]],
    model: str,
    api_key: str,
    base_url: str = "",
    api_mode: str = "responses",
    user_agent: str = "",
    batch_size: int = 3,
    max_retries: int = 3,
    reasoning_effort: str = "",
) -> dict[str, dict[str, Any]]:
    from openai import OpenAI

    client_options: dict[str, Any] = {
        "api_key": api_key,
        "timeout": 120.0,
        "max_retries": max(0, max_retries),
    }
    if base_url:
        client_options["base_url"] = base_url
    if user_agent:
        client_options["default_headers"] = {"User-Agent": user_agent}
    client = OpenAI(**client_options)
    paper_payload = [
        {
            "paper_id": paper["id"],
            "title": paper["title"],
            "abstract": paper["abstract"][:3200],
            "categories": paper.get("categories", []),
            "heuristic_tags": paper.get("tags", []),
        }
        for paper in papers
    ]
    system_prompt = (
        "你是一位严谨的宇宙学文献编辑，面向研究 CMB 的中文读者。"
        "只能依据提供的题目、摘要和分类进行解读，不得假装读过全文，不得补造数值、显著性或结论。"
        "将标题准确翻译为中文；summary_zh 用 1-2 句说明问题、方法和摘要所报告的结果；"
        "why_it_matters_zh 解释它与 CMB/宇宙学研究的关系；key_points 给 2-3 条短句；"
        "methods 提取摘要明确出现的方法或数据；reading_note_zh 指出精读时最该核对的问题。"
        "audience 使用“快速浏览”“领域相关”或“建议精读”。confidence 表示仅凭摘要作此解读的信心。"
    )
    if api_mode not in {"responses", "chat_completions"}:
        raise ValueError("GPT_API_MODE must be 'responses' or 'chat_completions'")

    batch_size = max(1, batch_size)
    batch_total = (len(paper_payload) + batch_size - 1) // batch_size
    parsed_items: list[PaperAnalysis] = []
    for batch_index, start in enumerate(range(0, len(paper_payload), batch_size), start=1):
        batch_payload = paper_payload[start : start + batch_size]
        LOGGER.info(
            "Requesting GPT batch %s/%s (%s papers)",
            batch_index,
            batch_total,
            len(batch_payload),
        )
        user_content = (
            "请逐篇分析以下论文，并保持 paper_id 完全不变：\n"
            + json.dumps(batch_payload, ensure_ascii=False)
        )
        if api_mode == "responses":
            response_options: dict[str, Any] = {
                "model": model,
                "input": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "text_format": AnalysisBatch,
            }
            if reasoning_effort:
                response_options["reasoning"] = {"effort": reasoning_effort}
            response = client.responses.parse(**response_options)
            parsed = response.output_parsed
        else:
            schema_prompt = (
                "\n必须只返回一个 JSON 对象，不要使用 Markdown。JSON 必须严格符合此 schema：\n"
                + json.dumps(AnalysisBatch.model_json_schema(), ensure_ascii=False)
            )
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content + schema_prompt},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            parsed = AnalysisBatch.model_validate_json(content)
        if parsed is None:
            raise RuntimeError(f"GPT endpoint returned no parsed analysis for batch {batch_index}")
        requested_ids = {paper["paper_id"] for paper in batch_payload}
        returned_ids = {item.paper_id for item in parsed.analyses}
        missing_ids = requested_ids - returned_ids
        if missing_ids:
            raise RuntimeError(
                f"GPT response omitted {len(missing_ids)} requested papers in batch {batch_index}"
            )
        parsed_items.extend(parsed.analyses)

    now = iso_now()
    result: dict[str, dict[str, Any]] = {}
    valid_ids = {paper["id"] for paper in papers}
    for item in parsed_items:
        if item.paper_id not in valid_ids:
            continue
        payload = item.model_dump(exclude={"paper_id"})
        result[item.paper_id] = {
            "provider": "openai",
            "model": model,
            "basis": "abstract",
            "generated_at": now,
            **payload,
        }
    return result


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.warning("Could not read %s: %s", path, exc)
        return default


def select_current(
    candidates: list[dict[str, Any]],
    config: dict[str, Any],
    now: datetime,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    lookback = now - timedelta(days=int(config.get("lookback_days", 21)))
    recent = [paper for paper in candidates if parse_datetime(paper["published"]) >= lookback]
    focus = sorted(
        (paper for paper in recent if paper["track"] == "focus"),
        key=lambda paper: (
            str(paper["published"])[:10],
            paper["scores"]["editorial"],
            paper["published"],
        ),
        reverse=True,
    )[: int(config.get("focus_limit", 12))]
    focus_ids = {paper["id"] for paper in focus}
    discovery = sorted(
        (paper for paper in recent if paper["id"] not in focus_ids),
        key=lambda paper: (
            str(paper["published"])[:10],
            paper["scores"]["interest"],
            paper["published"],
        ),
        reverse=True,
    )[: int(config.get("discovery_limit", 6))]
    return focus + discovery, [paper["id"] for paper in focus], [paper["id"] for paper in discovery]


def select_archive(
    candidates: list[dict[str, Any]],
    config: dict[str, Any],
    now: datetime,
    days: int,
) -> list[dict[str, Any]]:
    cutoff = now - timedelta(days=days)
    by_month: dict[str, list[dict[str, Any]]] = {}
    for paper in candidates:
        try:
            published = parse_datetime(paper["published"])
        except (KeyError, ValueError):
            continue
        if published < cutoff:
            continue
        by_month.setdefault(published.strftime("%Y-%m"), []).append(paper)

    focus_limit = int(config.get("archive_focus_per_month", 32))
    discovery_limit = int(config.get("archive_discovery_per_month", 12))
    selected: list[dict[str, Any]] = []
    for month in sorted(by_month, reverse=True):
        monthly = by_month[month]
        focus = sorted(
            (paper for paper in monthly if paper["track"] == "focus"),
            key=lambda paper: (paper["scores"]["editorial"], paper["published"]),
            reverse=True,
        )[:focus_limit]
        focus_ids = {paper["id"] for paper in focus}
        discovery = sorted(
            (paper for paper in monthly if paper["id"] not in focus_ids),
            key=lambda paper: (paper["scores"]["interest"], paper["scores"]["editorial"], paper["published"]),
            reverse=True,
        )[:discovery_limit]
        selected.extend(focus + discovery)

    deduplicated = {paper["id"]: paper for paper in selected}
    return sorted(deduplicated.values(), key=lambda paper: paper["published"], reverse=True)


def find_new_or_updated(
    selected: list[dict[str, Any]],
    existing: dict[str, Any],
) -> list[dict[str, Any]]:
    previous = {paper["id"]: paper for paper in existing.get("papers", []) if paper.get("id")}
    return [
        paper
        for paper in selected
        if paper["id"] not in previous
        or paper.get("content_hash") != previous[paper["id"]].get("content_hash")
    ]


def merge_history(
    selected: list[dict[str, Any]],
    existing: dict[str, Any],
    config: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    previous = {paper["id"]: paper for paper in existing.get("papers", []) if paper.get("id")}
    for paper in selected:
        old = previous.get(paper["id"])
        if old and old.get("content_hash") == paper.get("content_hash") and old.get("analysis"):
            paper["analysis"] = old["analysis"]
            paper["first_selected_at"] = old.get("first_selected_at", iso_now())
        else:
            paper["analysis"] = fallback_analysis(paper)
            paper["first_selected_at"] = old.get("first_selected_at", iso_now()) if old else iso_now()
        paper["last_selected_at"] = iso_now()
        previous[paper["id"]] = paper

    cutoff = now - timedelta(days=int(config.get("history_days", 120)))
    retained = []
    for paper in previous.values():
        try:
            if parse_datetime(paper["published"]) >= cutoff:
                retained.append(paper)
        except (KeyError, ValueError):
            continue
    retained.sort(key=lambda paper: paper.get("published", ""), reverse=True)
    return retained[: int(config.get("max_history", 180))]


def update_data(args: argparse.Namespace) -> UpdateOutcome:
    config_path = Path(args.config)
    output_path = Path(args.output)
    config = read_json(config_path, {})
    if not config.get("queries"):
        raise ValueError(f"No queries configured in {config_path}")
    existing = read_json(output_path, {"meta": {}, "papers": []})
    settings = gpt_settings(args)
    if args.require_ai and not settings["api_key"]:
        LOGGER.info("Skipping update: GPT API key is not configured")
        return UpdateOutcome(existing, False, "missing_api_key")

    now = datetime.now(timezone.utc)
    backfill_days = max(0, int(getattr(args, "backfill_days", 0) or 0))

    existing_meta = existing.get("meta", {})
    reuse_existing_archive = bool(
        backfill_days
        and existing.get("papers")
        and int(existing_meta.get("archive_days", 0) or 0) >= backfill_days
        and int(existing_meta.get("archive_pending_count", 0) or 0) > 0
    )
    if reuse_existing_archive:
        LOGGER.info("Continuing GPT analysis for the existing %s-day archive", backfill_days)
        fetched = []
        errors = list(existing_meta.get("fetch_errors", []))
        papers = existing["papers"]
        focus_ids = list(existing_meta.get("current_focus_ids", []))
        discovery_ids = list(existing_meta.get("current_discovery_ids", []))
        new_or_updated: list[dict[str, Any]] = []
    else:
        fetched, errors = fetch_all(
            config,
            args.max_results,
            backfill_days=backfill_days,
            now=now,
        )
        if not fetched:
            if existing.get("papers"):
                LOGGER.warning("All arXiv requests failed; keeping the last successful dataset unchanged")
                return UpdateOutcome(existing, False, "arxiv_unavailable")
            raise RuntimeError("All arXiv requests failed and no previous dataset is available")

        scored = [score_paper(paper, now) for paper in fetched]
        current_selected, focus_ids, discovery_ids = select_current(scored, config, now)
        selected = select_archive(scored, config, now, backfill_days) if backfill_days else current_selected
        new_or_updated = find_new_or_updated(selected, existing)
        if args.skip_if_no_new and not new_or_updated:
            LOGGER.info("Skipping update: no new or revised selected papers")
            return UpdateOutcome(existing, False, "no_new_papers")
        papers = merge_history(selected, existing, config, now)

    selected_order = {paper_id: index for index, paper_id in enumerate(focus_ids + discovery_ids)}
    selected_papers = sorted(
        (paper for paper in papers if paper["id"] in selected_order),
        key=lambda paper: selected_order[paper["id"]],
    )
    ai_key_present = bool(settings["api_key"]) and not args.no_ai
    configured_limit = int(config.get("analysis_limit", 12))
    analysis_cap = getattr(args, "analysis_cap", None)
    analysis_limit = configured_limit if analysis_cap is None else max(1, int(analysis_cap))
    if backfill_days:
        needs_analysis = [
            paper
            for paper in papers
            if paper.get("analysis", {}).get("provider") != "openai"
        ][:analysis_limit]
    elif args.force_ai:
        needs_analysis = selected_papers[:analysis_limit]
    else:
        needs_analysis = [
            paper
            for paper in selected_papers
            if paper.get("analysis", {}).get("provider") != "openai"
        ][:analysis_limit]

    model = settings["model"]
    ai_error = ""
    if ai_key_present and needs_analysis:
        LOGGER.info(
            "Requesting GPT analysis for %s papers with %s via %s",
            len(needs_analysis),
            model,
            settings["api_mode"],
        )
        try:
            analyses = analyze_with_openai(
                needs_analysis,
                model,
                api_key=settings["api_key"],
                base_url=settings["base_url"],
                api_mode=settings["api_mode"],
                user_agent=settings["user_agent"],
                batch_size=int(settings["batch_size"]),
                max_retries=int(settings["max_retries"]),
                reasoning_effort=settings["reasoning_effort"],
            )
            missing_ids = {paper["id"] for paper in needs_analysis} - set(analyses)
            if missing_ids:
                raise RuntimeError(f"GPT response omitted {len(missing_ids)} requested papers")
            for paper in papers:
                if paper["id"] in analyses:
                    paper["analysis"] = analyses[paper["id"]]
        except Exception as exc:
            ai_error = str(exc)
            if args.require_ai:
                LOGGER.exception("GPT analysis failed; keeping the published dataset unchanged")
                return UpdateOutcome(existing, False, "ai_error", len(new_or_updated))
            LOGGER.exception("GPT analysis failed; using metadata-only fallback")
    elif args.require_ai:
        LOGGER.info("Skipping update: strict AI mode found nothing to analyze")
        return UpdateOutcome(existing, False, "nothing_to_analyze", len(new_or_updated))

    current_ai_count = sum(
        1
        for paper in selected_papers
        if next((item for item in papers if item["id"] == paper["id"]), paper)
        .get("analysis", {})
        .get("provider")
        == "openai"
    )
    analysis_status = "openai" if current_ai_count else "fallback"
    if current_ai_count and current_ai_count < len(selected_papers):
        analysis_status = "mixed"
    archive_ai_count = sum(
        1 for paper in papers if paper.get("analysis", {}).get("provider") == "openai"
    )
    archive_pending_count = len(papers) - archive_ai_count
    if archive_ai_count and archive_pending_count:
        analysis_status = "mixed"
    archive_dates = sorted(
        {str(paper.get("published", ""))[:10] for paper in papers if paper.get("published")},
        reverse=True,
    )
    archive_months = sorted({date[:7] for date in archive_dates}, reverse=True)
    previous_archive_days = int(existing.get("meta", {}).get("archive_days", 0) or 0)
    archive_days = backfill_days or previous_archive_days or int(config.get("history_days", 120))

    data = {
        "meta": {
            "generated_at": iso_now(),
            "last_attempt_at": iso_now(),
            "fetch_status": "ok" if not errors else "partial",
            "fetch_errors": errors,
            "source": "arXiv API",
            "source_url": "https://info.arxiv.org/help/api/",
            "analysis_status": analysis_status,
            "analysis_model": model if ai_key_present else None,
            "analysis_api_mode": settings["api_mode"] if ai_key_present else None,
            "analysis_endpoint": "custom" if settings["base_url"] else "openai",
            "analysis_error": ai_error,
            "analysis_basis": "title + abstract + categories",
            "lookback_days": int(config.get("lookback_days", 21)),
            "candidate_count": len(fetched) or int(existing_meta.get("candidate_count", 0) or 0),
            "paper_count": len(papers),
            "archive_days": archive_days,
            "archive_start": archive_dates[-1] if archive_dates else None,
            "archive_end": archive_dates[0] if archive_dates else None,
            "archive_dates": archive_dates,
            "archive_months": archive_months,
            "archive_ai_count": archive_ai_count,
            "archive_pending_count": archive_pending_count,
            "current_focus_ids": focus_ids,
            "current_discovery_ids": discovery_ids,
        },
        "papers": papers,
    }
    return UpdateOutcome(data, True, "updated", len(new_or_updated))


def write_github_outputs(outcome: UpdateOutcome) -> None:
    output_path = os.getenv("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"changed={'true' if outcome.changed else 'false'}\n")
        handle.write(f"reason={outcome.reason}\n")
        handle.write(f"new_count={outcome.new_count}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/radar.json")
    parser.add_argument("--output", default="site/data/papers.json")
    parser.add_argument("--model", default="")
    parser.add_argument("--max-results", type=int, default=None, help="Override each query size for local testing")
    parser.add_argument("--backfill-days", type=int, default=0, help="Build a balanced archive covering this many days")
    parser.add_argument("--analysis-cap", type=int, default=None, help="Maximum GPT analyses in this run")
    parser.add_argument("--no-ai", action="store_true", help="Skip GPT even when an API key is set")
    parser.add_argument("--require-ai", action="store_true", help="Keep the current dataset if GPT is unavailable")
    parser.add_argument("--skip-if-no-new", action="store_true", help="Do not write data when no selected paper is new or revised")
    parser.add_argument("--force-ai", action="store_true", help="Reanalyze current selections even when analysis already exists")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    try:
        outcome = update_data(args)
        write_github_outputs(outcome)
        if outcome.changed:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(outcome.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        LOGGER.exception("Update failed: %s", exc)
        return 1
    if not outcome.changed:
        LOGGER.info("No files changed (%s)", outcome.reason)
        return 0
    LOGGER.info(
        "Wrote %s papers to %s (%s; %s new or revised)",
        len(outcome.data.get("papers", [])),
        args.output,
        outcome.data.get("meta", {}).get("analysis_status", "unknown"),
        outcome.new_count,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
