"""Why Vigil classified something the way it did — in the reader's language.

The detector writes one explanation per finding ("Known directory: node_modules",
"Installed application in Program Files — …"). It is the evidence line in the
inspector, so it is read more than almost anything else on the screen, and it
was always English.

Translating it where it is *generated* would be wrong: a scan is saved to disk
and reopened later, possibly after the user changes language, and the stored
text would stay frozen in whatever language was active when the scan ran. So
the template and its values are stored, and the sentence is composed in the
reader's language at display time.

``Reason`` subclasses ``str`` deliberately. ``risk_reason`` is consumed all
over — the AI prompt embeds it, session JSON serialises it, the game-saves
pass does ``.startswith("entity type:")`` on it — and every one of those keeps
working unchanged on a str subclass, while the UI gets ``.key`` / ``.args`` to
re-render from.
"""
from __future__ import annotations


class Reason(str):
    """Rendered English text that remembers the template it came from."""

    __slots__ = ("key", "args")

    def __new__(cls, key: str, **args):
        try:
            text = key.format(**args) if args else key
        except (KeyError, IndexError, ValueError):
            text = key
        obj = super().__new__(cls, text)
        obj.key = key
        obj.args = args
        return obj

    def __reduce__(self):
        # Keep key/args across pickling; plain str.__reduce__ would drop them.
        return (_rebuild_reason, (self.key, self.args))


def _rebuild_reason(key: str, args: dict) -> "Reason":
    return Reason(key, **(args or {}))


def reason_key_of(value) -> str:
    """The template behind *value*, or "" for a plain string."""
    return getattr(value, "key", "") or ""


def reason_args_of(value) -> dict:
    return dict(getattr(value, "args", None) or {})


def translate_reason(entity: dict) -> str:
    """The explanation for *entity*, in the active language.

    Falls back to the stored English for sessions saved before reasons carried
    their template — those cannot be re-rendered, and showing the English is
    better than showing nothing.
    """
    from app.i18n import tr

    key = (entity.get("reason_key") or "").strip()
    if not key:
        return entity.get("risk_reason", "") or ""
    args = entity.get("reason_args") or {}
    try:
        return tr(key, **args) if args else tr(key)
    except Exception:
        return entity.get("risk_reason", "") or key
