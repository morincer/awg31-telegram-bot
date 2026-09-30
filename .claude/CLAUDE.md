# awg31-telegram-bot

A Telegram bot that adds AmneziaWG 3.x clients to a kernel-module server on the fly. The repository is public: no server names, addresses, tokens or real keys go into code, tests or fixtures.

## Tests are intent-based

A test states what the bot promises to its user or to the server, and checks that promise from the outside. It does not restate how the code happens to do it.

- **Name the intent.** A test name reads as a promise: `test_a_stranger_gets_no_answer`, `test_the_old_config_stops_working_after_reissue`. Not `test_admin_filter_returns_false`, not `test_handler_count`.
- **Check through the public surface.** Commands go in as Telegram updates through the dispatcher; what comes out is what Telegram would receive and what the interface and the peers file hold. Private helpers, handler registries, call counts and internal constants are not asserted.
- **The oracle is independent of the implementation.** A format the AmneziaVPN app reads is checked by a reader ported from the app's C++ (`tests/app_reader.py`), never by a decoder from `awg31_bot` — an encoder and a decoder written by the same hand share the same misreading and pass together. A QR image is checked by decoding the image, not by comparing it with the text that produced it.
- **Fakes model the other side, not the code.** A fake `awg` behaves like an interface (peers come and go, the running set can change); it does not script the exact calls the implementation makes. Exact argv or stdin is asserted only where it is the contract itself — a key that must never appear in a process's arguments.
- **Failure cases state what must survive.** A failed write leaves the previous peers file intact and the interface as it was; that is the assertion, not which function raised.
- **What only the real system can show is tested on it.** Whether an issued config actually connects, and whether a reissued or deleted one stops, is the `integration` suite: root, the `amneziawg` module, throwaway network namespaces. CI has no module and skips it; it is run on a node before a release.
- **Coverage is a floor, not a goal.** `--cov-fail-under=85` stays; a test written to reach a line, not to pin a promise, is removed even if coverage drops.
