# GitHub

A separate native GitHub client written in Ziran with Kryon. Organizations,
repositories and collaboration stay inside the client. Harmony embeds the
application window in its Apps view; it does not replace it with a chat.

The client includes:

- Organization navigation, accessible repositories, starred repositories and search.
- Stars, forks, visibility, primary language and repository language percentages.
- README, directories, source files and branch selection.
- Issues, pull requests, comments and patches, commit history, releases and Actions jobs.
- Notifications that open their issue, pull request or commit in the client.
- Star/unstar, new issues and comments through explicit buttons. Editors have a Close button.
- Independent content scrolling, provider pagination, back navigation and narrow layouts.
- Native app state and control bounds for Harmony's existing app harness.

The initial client does not yet edit repository files, merge pull requests,
manage organization settings or expose every GitHub administration feature.
Markdown and patches are displayed as selectable text. Binary and oversized
files have explicit preview messages. Notifications require GitHub CLI's
notification permission.

## Build and run

Install Ziran, Python 3, GitHub CLI, SDL2, Cairo, FreeType and X11 development
libraries. Sign in with `gh auth login`; credentials remain with GitHub CLI.
The app never asks for or extracts a token.

```sh
ziran fetch --locked
make build
make run
make install
github-client
```

For local development, use ignored `ziran.local.toml` overrides pointing to
the canonical Ziran, Kryon and KSS checkouts. Dependencies and the compiler
are pinned in `ziran.lock`. `make install` places a desktop entry and the
`github-client` command under `~/.local`.

The UI and navigation are Ziran. A small Python worker runs `gh api` using
argument vectors so network requests do not block UI frames. It validates
routes, uses private request files and gives the UI sanitized responses.
No repository text or search input becomes a shell command. API behavior
follows [GitHub's official REST documentation](https://docs.github.com/en/rest).

## Verify

```sh
make test
make window-test
```

Window tests scrub inherited display and session variables, start a private
Xvfb server, use a fixture transport and capture only the window they launch.
They cover navigation, code scrolling, back, closing the issue editor and a
narrow layout. API tests cover pagination, files, language data, notifications,
PR patches, jobs, explicit writes and write receipts after refresh failure.
