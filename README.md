# Podbye

Podbye is a Windows desktop utility for understanding storage use, reviewing
cleanup candidates, and managing startup items before making changes.

It is currently in beta. Review recommendations and selected targets before
removing anything.

## What to expect from the Beta

Podbye is designed to be cautious: when it cannot classify something with
enough confidence, it leaves it for review instead of treating it as safe to
clean.

Some applications, files, or components may still be classified incorrectly or
remain unrecognized, especially in unusual or unsupported layouts. Some workflows, interface details, or translations may still need refinement as Podbye is used on more real-world systems.

Please report anything incorrect, confusing, or unexpected through
[GitHub Issues](https://github.com/nazar-hetman/Podbye/issues). A screenshot
and a short description of what you expected are especially useful.

## Install

Use the Windows installer from [project releases](https://github.com/nazar-hetman/Podbye/releases)
for a standard installation, or download the portable ZIP and run `Podbye.exe`
from the extracted folder. Current Windows builds are unsigned, so SmartScreen
may warn on first run.

To run from source, see [DEVELOPMENT.md](DEVELOPMENT.md).

## Privacy and AI

Podbye has no cloud service or backend and sends no telemetry.

AI is optional; analysis, cleanup, and startup management work without it.

Analysis and cleanup run on your computer. AI features can run directly on your
computer, or you can configure an AI runtime on another computer you own on your
private local network, such as Ollama. Podbye does not use public AI endpoints.
When you choose a private-LAN AI server, the information needed for that AI
analysis is sent to the server you configured over your local network.

## Cleanup safety

Cleanup uses the Windows Recycle Bin by default. Emptying the Recycle Bin is an
optional, separately confirmed permanent action and cannot be undone.

Choosing the Recycle Bin is not consent to permanent deletion. Windows removes
an item outright when it exceeds the drive's Recycle Bin quota, or when the bin
is switched off for that drive, and reports it as a success either way. Before
moving an item, Podbye checks the Recycle Bin policy and skips targets known not
to fit. For large items, it also verifies the result when Windows provides
enough information and reports items that did not reach the Recycle Bin.

Protected and ignored paths are excluded from cleanup recommendations. Podbye
shows the target and method before it acts.

## Languages

The interface is available in English, Ukrainian, Spanish, German, French, and
Polish. The operator feed and canonical storage categories intentionally remain
English where they represent technical or product identifiers.

## Support Podbye

If Podbye is useful to you and you'd like to support its continued development, you can do so on
[Ko-fi](https://ko-fi.com/nazarhetman). It is entirely optional.

## Documentation

- [Development guide](DEVELOPMENT.md)
- [Build and release guide](BUILD.md)
- [Contributing](CONTRIBUTING.md)
- [Semantic pipeline](SEMANTIC_PIPELINE.md)
- [UI design rules](DESIGN_RULES.md)

## License

Podbye is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE).
See [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) for bundled dependency
notices.
