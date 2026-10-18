# Contributing

Thanks for helping with RemotePhone. A few things worth knowing before you open a pull request.

## Before you start

- A small fix: open a pull request directly.
- Anything larger, such as a new client, a protocol change or a new dependency: open an issue first and describe what you want to do. It saves work on both sides.

## One protocol, several clients

The Android app is the server. Every client, desktop or otherwise, speaks the protocol described in the README under **Protocol**.

- A change to the protocol goes into the phone app and every client in the same pull request, together with the README section. A release of the app and a release of the clients must work with each other.
- Every decision about who may watch or control the phone is made on the phone. A client never gets a way around approval, the challenge or the certificate binding.

## Code

- Comment only what the code does not say by itself.
- No new dependency when the standard library or a few lines do the job. The desktop client has four runtime dependencies, listed in `pyproject.toml`; keep it that way.
- Non-trivial logic comes with the smallest check that fails when it breaks: a plain script with asserts in `tests/`, run from the repo root with `python -m tests.<name>`. No test framework.
- Keep the diff to the change at hand. Unrelated cleanups go in their own pull request.

## Pull requests

- Say what you tested and on what: phone model and Android version, and the client's OS or browser.
- Mention whether the protocol changed. If it did, see above.
- For anything that touches the desktop client, run `python -m tests.test_input_handler` and `python -m tests.test_video_widget`.

Pull requests are squash merged, so commit messages on your branch can be whatever helps you work.

How to run the desktop client and the Android app is in the README under **Setup**.

By contributing you agree that your work is released under the MIT license in `LICENSE`.
