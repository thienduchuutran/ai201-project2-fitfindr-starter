"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


# Shared model for both LLM-backed tools (kept in one place so it's easy to swap).
_MODEL = "llama-3.3-70b-versatile"


def _complete(prompt: str, temperature: float, retries: int = 1) -> str:
    """
    Send a single user prompt to Groq and return the model's text reply.

    `retries` extra attempts are made on failure because a transient network/API
    blip shouldn't sink the whole interaction. If every attempt fails this re-raises
    the last error — the calling tool is responsible for converting that into a
    user-facing string, since the tools themselves must never raise.
    """
    client = _get_groq_client()
    last_error = None
    for _ in range(retries + 1):                  # +1 so retries=1 means two tries total
        try:
            response = client.chat.completions.create(
                model=_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,           # caller chooses creativity per tool
            )
            return response.choices[0].message.content.strip()
        except Exception as error:                 # broad by design: any failure → retry
            last_error = error
    raise last_error                               # exhausted attempts; caller will catch


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform

    TODO:
        1. Load all listings with load_listings().
        2. Filter by max_price and size (if provided).
        3. Score each remaining listing by keyword overlap with `description`.
        4. Drop any listings with a score of 0 (no relevant matches).
        5. Sort by score, highest first, and return the listing dicts.

    Before writing code, fill in the Tool 1 section of planning.md.
    """
    listings = load_listings()
    keywords = description.lower().split()        # tokenize once; matching is case-insensitive
    matches = []

    for listing in listings:
        # price filter: drop anything over budget (only when a ceiling was given)
        if max_price is not None and listing["price"] > max_price:
            continue

        # size filter: substring (not ==) and both sides lowered, so "M" matches
        # "S/M" and the dataset's free-form sizes like "XL (oversized)"
        if size is not None and size.lower() not in listing["size"].lower():
            continue

        # Build one searchable string per field. List fields are joined so a hit in
        # any tag/color counts once for that field; brand may be None → coalesce to "".
        fields = [
            listing["title"].lower(),
            listing["description"].lower(),
            " ".join(listing["style_tags"]).lower(),
            " ".join(listing["colors"]).lower(),
            (listing["brand"] or "").lower(),
        ]
        # score = number of fields containing at least one keyword (sum of bools, 0–5)
        score = sum(any(kw in field for kw in keywords) for field in fields)

        if score > 0:                              # 0 means no keyword overlap → not a match
            matches.append((score, listing))

    # highest score first; sorted() is stable so equal scores keep dataset order
    matches.sort(key=lambda pair: pair[0], reverse=True)
    return [listing for _, listing in matches]     # callers want the dicts, not the scores


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offer general styling advice for the item
        rather than raising an exception or returning an empty string.

    TODO:
        1. Check whether wardrobe['items'] is empty.
        2. If empty: call the LLM with a prompt for general styling ideas
           (what kinds of items pair well, what vibe it suits, etc.).
        3. If not empty: format the wardrobe items into a prompt and ask
           the LLM to suggest specific outfit combinations using the new item
           and named pieces from the wardrobe.
        4. Return the LLM's response as a string.

    Before writing code, fill in the Tool 2 section of planning.md.
    """
    items = wardrobe.get("items", [])             # tolerate a missing "items" key

    # Block describing the item under consideration — the spec requires the model to
    # see title, price, platform, condition, and style tags.
    item_block = (
        f"NEW ITEM the user is considering:\n"
        f"- {new_item['title']}\n"
        f"- Price: ${new_item['price']:.2f} on {new_item['platform']}\n"
        f"- Condition: {new_item['condition']}\n"
        f"- Style tags: {', '.join(new_item['style_tags'])}\n"
    )

    if not items:
        # Empty wardrobe is a valid new-user state — ask for general advice rather
        # than specific combos, so we still return a useful, non-empty string.
        prompt = (
            "You are a friendly personal stylist.\n\n"
            f"{item_block}\n"
            "The user hasn't added their wardrobe yet. Give general styling advice: "
            "what kinds of pieces pair well with this item, what vibe/aesthetic it "
            "suits, and 2-3 concrete styling tips. Keep it short and practical."
        )
    else:
        # Format each wardrobe piece on one line. Wardrobe items use 'name' (not
        # 'title') and carry 'notes' that are often None — a different shape from a
        # listing, so we read their fields explicitly here.
        wardrobe_lines = []
        for it in items:
            note = f" — notes: {it['notes']}" if it.get("notes") else ""   # skip None notes
            wardrobe_lines.append(
                f"- {it['name']} (colors: {', '.join(it['colors'])}; "
                f"style: {', '.join(it['style_tags'])}){note}"
            )
        wardrobe_block = "\n".join(wardrobe_lines)

        prompt = (
            "You are a friendly personal stylist.\n\n"
            f"{item_block}\n"
            f"THE USER'S WARDROBE:\n{wardrobe_block}\n\n"
            "Suggest 1-2 specific, complete outfit combinations that pair the new item "
            "with NAMED pieces from the wardrobe above. Reference the actual wardrobe "
            "items by name. Be specific, not generic."
        )

    try:
        return _complete(prompt, temperature=0.7)  # 0.7 = varied but still coherent
    except Exception:
        # _complete already retried once; reaching here means it genuinely failed.
        return "Couldn't generate outfit suggestion. Please try again."


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, return a descriptive error message
        string — do NOT raise an exception.

    The caption should:
    - Feel casual and authentic (like a real OOTD post, not a product description)
    - Mention the item name, price, and platform naturally (once each)
    - Capture the outfit vibe in specific terms
    - Sound different each time for different inputs (use higher LLM temperature)

    TODO:
        1. Guard against an empty or whitespace-only outfit string.
        2. Build a prompt that gives the LLM the item details and the outfit,
           and asks for a caption matching the style guidelines above.
        3. Call the LLM and return the response.

    Before writing code, fill in the Tool 3 section of planning.md.
    """
    # Guard first: a blank/whitespace outfit means upstream produced nothing to
    # caption, so return a clear message instead of calling the LLM (or raising).
    if not outfit or not outfit.strip():
        return "Outfit data is missing — can't generate a fit card."

    prompt = (
        "Write a short, casual Instagram/TikTok caption (2-4 sentences) for a "
        "thrifted outfit. Sound like a real OOTD post — authentic and a little "
        "playful, NOT a product description.\n\n"
        f"THE FIND: {new_item['title']} — ${new_item['price']:.2f} on {new_item['platform']}\n"
        f"THE OUTFIT: {outfit}\n\n"
        "Mention the item name, the price, and the platform naturally, once each. "
        "Capture the vibe in specific terms."
    )

    try:
        return _complete(prompt, temperature=1.0)  # 1.0 = high creativity → fresh each run
    except Exception:
        return "Couldn't generate a fit card. Please try again."
