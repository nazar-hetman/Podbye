"""AI prompt quality and generation guards.

The model output itself can't be tested here (Ollama is the user's local
machine), so these pin the two things that ARE in our control: the instructions
we send, and how we post-process the reply.
"""
import re

from app.services import prompt_builder as pb
from app.services.prompt_builder import build_prompt, build_entity_prompt
from app.services.ollama_client import strip_reasoning
from app.services.ai_explainer import _gen_options


def _p(**kw):
    base = dict(path="C:/Users/x/.codeium", name=".codeium", is_dir=True,
               size="120 MB", category="Dev Artifacts", risk="Review",
               source_rule="", modified="2025-01-01", accessed="2025-01-01")
    base.update(kw)
    return build_prompt(**base)


# ── the .codeium failure: circular / uninformative identity ──────


def test_prompt_forbids_defining_by_repeating_the_name():
    """The reported bug: 'codeium contains files for codeium'. The prompt must
    explicitly rule that out."""
    p = _p().lower()
    assert "repeating its own name" in p or "x contains files for x" in p


def test_prompt_demands_plain_identity_and_purpose():
    p = _p().lower()
    assert "what kind of tool or data" in p
    assert "what people use it for" in p


def test_prompt_offers_known_equivalent_escape_hatch():
    """Small local models often don't know niche tools by name but do know the
    category — telling them to name a well-known equivalent is what turns a
    circular answer into a useful one."""
    p = _p().lower()
    assert "well-known equivalent" in p
    assert "copilot" in p  # the concrete example that anchors the instruction


def test_directness_rule_reaches_entity_prompts_too():
    ep = build_entity_prompt(
        path="C:/x/.codeium", name=".codeium", entity_type="unknown_folder",
        entity_type_label="Folder", size="120 MB", file_count=40, folder_count=3,
        category="Dev Artifacts", risk="Review", children_sample=["a", "b"],
    ).lower()
    assert "well-known equivalent" in ep
    assert "repeating its own name" in ep


def test_prompt_stays_compact():
    """'Informative not long' — the instruction block shouldn't balloon. Keep
    the whole compact prompt well under a small-model-friendly budget."""
    p = _p(length="compact")
    assert len(p) < 1200, f"compact prompt is {len(p)} chars — trim it"


# ── reasoning-model output stripping ─────────────────────────────


def test_strip_reasoning_removes_think_block():
    raw = "<think>Let me consider what .codeium is...</think>Codeium is an AI code tool."
    assert strip_reasoning(raw) == "Codeium is an AI code tool."


def test_strip_reasoning_handles_unterminated_block():
    """A reply truncated mid-thought (num_predict hit) must not surface raw
    chain-of-thought to the user."""
    raw = "Codeium is an AI code tool.<think>now let me second-guess"
    assert strip_reasoning(raw) == "Codeium is an AI code tool."


def test_strip_reasoning_is_noop_without_think():
    raw = "Codeium is an AI code-completion tool similar to GitHub Copilot."
    assert strip_reasoning(raw) == raw


def test_strip_reasoning_is_case_and_multiline_insensitive():
    raw = "<THINK>\nmulti\nline\n</THINK>  Answer here."
    assert strip_reasoning(raw) == "Answer here."


# ── generation options ───────────────────────────────────────────


def test_gen_options_are_low_temperature_for_facts():
    for length in ("compact", "standard", "detailed"):
        opts = _gen_options(length)
        assert opts["temperature"] <= 0.3, "temperature too high for factual output"
        assert opts["num_predict"] > 0


def test_gen_options_scale_with_length():
    assert (_gen_options("compact")["num_predict"]
            < _gen_options("detailed")["num_predict"])


def test_gen_options_unknown_length_falls_back_to_standard():
    assert _gen_options("bogus") == _gen_options("standard")
