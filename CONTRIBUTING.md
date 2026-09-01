# Contributing to Podbye

Thanks for helping improve Podbye. Keep changes focused, reviewable, and safe
for people managing real files.

## Development setup

Use Windows with Python 3.12 or a compatible supported Python version.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest
```

Run the application locally with `python main.py`. See [DEVELOPMENT.md](DEVELOPMENT.md)
and [BUILD.md](BUILD.md) for more detail.

## Pull requests

- Describe the user-visible behavior and the safety impact of the change.
- Keep cleanup actions explicit. Recycle Bin behavior is the default; permanent
  deletion must remain opt-in, confirmed, and clearly irreversible.
- Add or update tests for behavior changes. Run the relevant tests before
  submitting.
- Do not commit local databases, logs, caches, build output, credentials, or
  machine-specific paths.

## Localization

Translate from the English semantic source and inspect call sites when a term is
ambiguous. Keep canonical category names, paths, URLs, model identifiers,
program/publisher names, diagnostic output, and other raw external data
unchanged. Localize application-defined values when presenting them, without
changing their persisted canonical form. Check text at the minimum supported
window width.

## License

By contributing, you agree that your contribution may be distributed under the
repository's [PolyForm Noncommercial license](LICENSE). Ensure that any new dependency or asset
has licensing compatible with that distribution.
