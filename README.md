# Podbye

Podbye is a Windows desktop utility for understanding storage use, reviewing
cleanup candidates, and managing startup items before making changes.

It is currently in beta. Review recommendations and selected targets before
removing anything.

## Install

Use the Windows installer from the project releases for a standard installation,
or download the portable ZIP and run `Podbye.exe` from the extracted folder.

To run from source, see [DEVELOPMENT.md](DEVELOPMENT.md).

## Privacy and AI

Podbye has no cloud service or backend and sends no telemetry.

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
is switched off for that drive, and reports it as a success either way. Podbye
checks that before it acts: an item the bin would not accept is left on disk and
reported, rather than destroyed to satisfy the request. Permanent deletion
remains a separate mode you turn on deliberately.

Protected and ignored paths are excluded from cleanup recommendations. Podbye
shows the target and method before it acts.

## Languages

The interface is available in English, Ukrainian, Spanish, German, French, and
Polish. The operator feed and canonical storage categories intentionally remain
English where they represent technical or product identifiers.

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
