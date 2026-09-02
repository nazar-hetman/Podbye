"""Verified components inside a known application root.

A folder's *place* inside an application whose identity has been proven is
evidence about that folder, in a way its name alone never is. ``buildtrees``
anywhere on disk is a word; ``buildtrees`` beside ``ports``, ``triplets`` and
``.vcpkg-root`` is a vcpkg build tree, and vcpkg regenerates it.

This module is the whole of that knowledge. It answers three questions and
nothing else:

    does this root prove itself?      → PROOFS
    which components does it hold?    → components_for()
    what may that component become?   → role_entity_type()

**Application identity may improve understanding. It must never by itself
increase deletion permission.** The proof decides only *whether a rule
applies*; the role alone decides what the finding becomes, and the resulting
entity type goes through the same ``actionability_for_type`` as everything
else. There is no path from "this is VS Code" to "therefore delete" — an app
Podbye recognises is not more disposable than one it does not.

Two things follow from that, and both are deliberate:

* Roles cover only content an application *regenerates*. Settings, saves,
  workspaces, installed games and project data have no role and never will;
  the existing ``component_rules`` labels already describe them in the
  inspector, which is the right amount of knowing.
* Every rule is matched **relative to a proven root**. No absolute path, no
  environment variable, no registry key is ever stored here, which is what
  lets a portable install, a custom drive and a version bump all keep
  working without a data change.

Scope is four components across two applications (see PROOFS). Adding a
fifth is a data edit plus a proof function, and the proof is the part that
matters: a rule that fires on the wrong tree is worse than no rule at all.
"""
from __future__ import annotations

import os
import time

# ── Roles ─────────────────────────────────────────────────────────
# The single point where a role becomes something with an action. Kept tiny
# and explicit: reading this table should tell you every destructive outcome
# this module can produce.
#
#   build_output    the application rebuilds it from sources it still has
#   download_cache  the application fetches it again over the network
#
# Both map onto types that already exist, so the risk taxonomy is untouched.
# installer_cache is Review rather than Safe on purpose — a re-download costs
# the user time and bandwidth, so it stays a decision instead of a default.
_ROLE_TYPES = {
    "build_output": "build_folder",       # Safe   · recycle
    "download_cache": "installer_cache",  # Review · recycle
}


def known_roles() -> tuple:
    return tuple(sorted(_ROLE_TYPES))


def role_entity_type(role: str) -> str:
    """The entity type a role produces, or "" when the role is unknown.

    An unknown role yields nothing rather than a default. Stale or partial
    knowledge must fail closed: a rule referring to a role this build does
    not implement is skipped, not guessed at.
    """
    return _ROLE_TYPES.get(role or "", "")


# ── Proofs ────────────────────────────────────────────────────────
# Each proof is structural. It asks the filesystem for things a real install
# has and a coincidence does not, and it must be cheap: a handful of exists()
# calls, no walking.

def _all_exist(root: str, *relatives: str) -> bool:
    for rel in relatives:
        if not os.path.exists(os.path.join(root, rel.replace("/", os.sep))):
            return False
    return True


def _any_exists(root: str, *relatives: str) -> bool:
    return any(
        os.path.exists(os.path.join(root, rel.replace("/", os.sep)))
        for rel in relatives
    )


def prove_vcpkg_root(root: str) -> str:
    """Evidence that *root* is a vcpkg instance, or "".

    ``ports`` and ``triplets`` together are already unusual, but both are
    ordinary English words and a source tree could hold either. The third
    requirement is a marker only vcpkg itself writes, so a folder has to look
    like vcpkg *and* carry vcpkg's own signature.

    Deliberately not accepted as proof: the folder being named "vcpkg". That
    is the name-only evidence this whole line of work exists to refuse.
    """
    if not root or not os.path.isdir(root):
        return ""
    if not _all_exist(root, "ports", "triplets"):
        return ""
    markers = (".vcpkg-root", "vcpkg.exe", "scripts/buildsystems/vcpkg.cmake")
    found = [m for m in markers
             if os.path.exists(os.path.join(root, m.replace("/", os.sep)))]
    if not found:
        return ""
    return f"ports + triplets + {found[0]}"


# A VS Code user-data root, by the folders VS Code itself creates there. The
# Electron trio plus its own User/ directory: a folder merely called "Code"
# with a cache in it does not qualify.
_VSCODE_SHAPE = ("User", "CachedExtensionVSIXs")
_VSCODE_ELECTRON = ("Cache", "CachedData", "GPUCache", "blob_storage",
                    "Local Storage", "logs")


def prove_vscode_user_data(root: str) -> str:
    """Evidence that *root* is a VS Code user-data directory, or "".

    Global app presence is explicitly *not* enough. "VS Code is installed
    somewhere on this machine" says nothing about whether this particular
    folder belongs to it, and a folder called Code on a data drive would
    have passed that test. The shape of the directory is the evidence.
    """
    if not root or not os.path.isdir(root):
        return ""
    if not _all_exist(root, *_VSCODE_SHAPE):
        return ""
    electron = [d for d in _VSCODE_ELECTRON
                if os.path.isdir(os.path.join(root, d))]
    if len(electron) < 3:
        return ""
    return f"User + CachedExtensionVSIXs + {len(electron)} Electron dirs"


PROOFS = {
    "vcpkg_root": prove_vcpkg_root,
    "vscode_user_data": prove_vscode_user_data,
}


def prove(proof_id: str, root: str) -> str:
    """Run a named proof. An unknown proof id proves nothing."""
    fn = PROOFS.get(proof_id or "")
    if fn is None:
        return ""
    try:
        return fn(root)
    except OSError:
        # An unreadable root is not a proven root.
        return ""


# ── Rules ─────────────────────────────────────────────────────────

def _rules_table() -> dict:
    """The ``component_roles`` table, or {} when this build has none.

    Imported lazily for the same reason entity_contents does it: the rules
    live beside the detector and importing it at module scope would be a
    cycle.
    """
    from app.services.entity_detector import _RULES
    raw = _RULES.get("component_roles")
    return raw if isinstance(raw, dict) else {}


class Component:
    """One proven component: where it is, what it is, and why we think so."""

    __slots__ = ("rule_id", "relative", "role", "label", "entity_type",
                 "root", "path", "evidence")

    def __init__(self, rule_id, relative, role, label, entity_type,
                 root, path, evidence):
        self.rule_id = rule_id
        self.relative = relative
        self.role = role
        self.label = label
        self.entity_type = entity_type
        self.root = root
        self.path = path
        self.evidence = evidence

    def __repr__(self):
        return f"<Component {self.rule_id} {self.path}>"


def components_for(root: str, proof_id: str = "") -> list:
    """Every proven component directly under *root*.

    Returns [] unless the root proves itself, so an unproven folder yields
    nothing at all rather than a best guess. *proof_id* restricts the search
    to one application when the caller already knows which it is.
    """
    if not root:
        return []
    table = _rules_table()
    if not table:
        return []

    proven: dict = {}
    out = []
    for rule_id, spec in sorted(table.items()):
        if not isinstance(spec, dict):
            continue
        needed = str(spec.get("root_proof") or "")
        if proof_id and needed != proof_id:
            continue
        role = str(spec.get("role") or "")
        entity_type = role_entity_type(role)
        if not entity_type:
            continue                       # unknown role: fail closed
        relative = str(spec.get("relative") or "").strip("/")
        if not relative:
            continue

        if needed not in proven:
            proven[needed] = prove(needed, root)
        evidence = proven[needed]
        if not evidence:
            continue

        path = os.path.join(root, relative.replace("/", os.sep))
        try:
            if not os.path.isdir(path):
                continue
        except OSError:
            continue
        out.append(Component(
            rule_id=rule_id, relative=relative, role=role,
            label=str(spec.get("label") or relative),
            entity_type=entity_type, root=root, path=path, evidence=evidence,
        ))
    return out


# ── Active-build heuristic ────────────────────────────────────────
# A conservative guess, never a guarantee. A build can start the instant
# after this returns, and nothing here can prevent that; what it prevents is
# offering to recycle a tree that is visibly mid-build. The real backstop is
# that cleanup moves to the Recycle Bin.

_BUILD_QUIET_SECONDS = 6 * 3600     # touched within 6h: treat as in use
_BUILD_LOCK_NAMES = ("vcpkg.lock", ".vcpkg-lock", "lockfile",
                     "vcpkg-lock.json")
_SAMPLE_DIRS = 12                   # depth-1 children sampled, newest wins


def build_looks_active(root: str, now: float = 0.0) -> str:
    """A reason to believe a build is running, or "" if none is visible.

    Errs toward "active": anything unreadable counts as busy, because
    refusing to offer a cleanup is the cheap mistake and offering one over a
    running build is not.
    """
    if not root:
        return "no root"
    now = now or time.time()

    for name in _BUILD_LOCK_NAMES:
        if os.path.exists(os.path.join(root, name)):
            return f"lock file present ({name})"

    for sub in ("buildtrees", "packages"):
        path = os.path.join(root, sub)
        if not os.path.isdir(path):
            continue
        try:
            entries = sorted(os.listdir(path))[:_SAMPLE_DIRS]
        except OSError:
            return f"{sub} could not be read"
        newest = 0.0
        for entry in entries:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(path, entry)))
            except OSError:
                return f"{sub}/{entry} could not be read"
        if newest and (now - newest) < _BUILD_QUIET_SECONDS:
            hours = (now - newest) / 3600
            return f"{sub} changed {hours:.1f}h ago"
    return ""
