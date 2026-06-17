"""
tests/test_tools.py

Milestone 3 — tests for the three FitFindr tools, exercised in isolation.

The search_listings tests use only local data, so they run without any API key.
The suggest_outfit / create_fit_card tests that actually call the LLM require
GROQ_API_KEY in a .env file at the project root. (test_create_fit_card_empty_outfit
hits the empty-input guard and returns before any API call, so it also runs offline.)

Run with:  pytest tests/ -v
"""

import os
import sys

# Make the project root importable no matter where pytest is launched from.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools import search_listings, suggest_outfit, create_fit_card
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe


# ── search_listings (no API key needed) ───────────────────────────────────────

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=None)
    assert len(results) > 0


def test_search_empty_results():
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=10)
    assert all(r["price"] <= 10 for r in results)


def test_search_size_filter():
    results = search_listings("jeans", size="M", max_price=None)
    assert all("m" in r["size"].lower() for r in results)


def test_search_relevance_order():
    results = search_listings("vintage", size=None, max_price=None)
    assert len(results) > 0
    top = results[0]
    assert "vintage" in [t.lower() for t in top["style_tags"]] or "vintage" in top["title"].lower()


# ── suggest_outfit + create_fit_card (need GROQ_API_KEY in .env) ───────────────

def test_suggest_outfit_with_wardrobe():
    item = search_listings("vintage graphic tee", size=None, max_price=None)[0]
    result = suggest_outfit(item, get_example_wardrobe())
    assert isinstance(result, str) and result.strip() != ""


def test_suggest_outfit_empty_wardrobe():
    item = search_listings("vintage graphic tee", size=None, max_price=None)[0]
    result = suggest_outfit(item, get_empty_wardrobe())
    assert isinstance(result, str) and result.strip() != ""


def test_create_fit_card_valid():
    item = search_listings("vintage graphic tee", size=None, max_price=None)[0]
    result = create_fit_card("pair with baggy jeans and chunky white sneakers", item)
    assert isinstance(result, str) and result.strip() != ""


def test_create_fit_card_empty_outfit():
    item = search_listings("vintage graphic tee", size=None, max_price=None)[0]
    result = create_fit_card("", item)
    assert result == "Outfit data is missing — can't generate a fit card."
