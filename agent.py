"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import json

from tools import search_listings, suggest_outfit, create_fit_card, _complete


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
    }


# ── query parsing ─────────────────────────────────────────────────────────────

def _extract_json(text: str) -> str:
    """
    Pull the outermost {...} object out of an LLM reply.

    Models sometimes wrap JSON in prose or ```json fences even when told not to,
    so we slice from the first '{' to the last '}' instead of trusting the whole
    string to be valid JSON. Raises ValueError if there are no braces at all.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in LLM response")
    return text[start : end + 1]


def _parse_query(query: str) -> dict:
    """
    Use the LLM to turn a natural-language request into structured search params:
        {"description": str, "size": str|None, "max_price": float|None}

    Falls back to {description=query, size=None, max_price=None} on ANY failure
    (LLM error, bad JSON, wrong types) so the planning loop can always proceed —
    query parsing must never crash the agent.
    """
    prompt = (
        "Extract search parameters from this secondhand-clothing request. "
        "Return ONLY a JSON object, no other text.\n\n"
        f'Request: "{query}"\n\n'
        "Shape (use null when a field is not mentioned):\n"
        '{"description": "<keywords for the item>", "size": "<size or null>", '
        '"max_price": <number or null>}'
    )
    try:
        raw = _complete(prompt, temperature=0)        # temp 0 → stable, structured output
        data = json.loads(_extract_json(raw))
        max_price = data.get("max_price")
        return {
            # description must never be empty, or search has nothing to match on;
            # fall back to the raw query if the model returns null/""
            "description": data.get("description") or query,
            "size": data.get("size"),                 # JSON null → Python None automatically
            # model may return max_price as a number or numeric string → coerce to float
            "max_price": float(max_price) if max_price is not None else None,
        }
    except Exception:
        # any failure → treat the whole query as keywords with no filters
        return {"description": query, "size": None, "max_price": None}


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    session = _new_session(query, wardrobe)

    # Step 2 — turn the raw query into structured params. Done with the LLM (with a
    # safe fallback) so phrasings like "under $30" or "size M" become real filters
    # instead of just keywords. _parse_query never raises.
    session["parsed"] = _parse_query(query)
    parsed = session["parsed"]

    # Step 3 — retrieval. search_listings is deterministic/local and returns [] (never
    # raises) when nothing matches — that empty list is the trigger for GATE 1 below.
    session["search_results"] = search_listings(
        parsed["description"], parsed["size"], parsed["max_price"]
    )
    if not session["search_results"]:
        # GATE 1: no matches → stop before any LLM tool runs, so we never feed empty
        # input downstream. This is the agent's first short-circuit.
        session["error"] = (
            f"No listings found for '{parsed['description']}'. "
            "Try broader keywords, a different size, or raising your price limit."
        )
        return session

    # Step 4 — selection. search_listings already sorted by relevance, so [0] is the
    # top match: the single item that flows into BOTH downstream LLM tools.
    session["selected_item"] = session["search_results"][0]

    # Step 5 — styling. Pass the selected item AND the wardrobe; suggest_outfit handles
    # the empty-wardrobe case internally (general advice) and never raises.
    session["outfit_suggestion"] = suggest_outfit(
        session["selected_item"], session["wardrobe"]
    )
    outfit = session["outfit_suggestion"]
    if not outfit or outfit.startswith("Couldn't"):
        # GATE 2: empty output OR the tool's "Couldn't generate..." failure fallback →
        # stop before create_fit_card, since a caption needs a real outfit to describe.
        session["error"] = "Outfit suggestion failed. Please try again."
        return session

    # Step 6 — caption. Feed the outfit text + selected item into the final tool.
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )
    if session["fit_card"].startswith("Outfit data is missing"):
        # defensive: only trips if an empty outfit somehow slipped past GATE 2
        session["error"] = session["fit_card"]
        return session

    # Step 7 — success: every slot populated, error stays None.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
