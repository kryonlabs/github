# GitHub client

The canonical checkout is `kryonlabs/github`, on master. This is a separate
Kryon application; Harmony embeds its own window and does not own its UI.

Write product UI and navigation in Ziran. The Python transport invokes `gh`
with an argument vector, leaving credentials with GitHub CLI. Never retrieve,
display or store access tokens. All destinations stay on GitHub's API, and
repository data never becomes a shell command. Mutations require a deliberate
client action. Do not merge PRs, push code or delete repositories on refresh.

Run visual verification only on a private Xvfb display after scrubbing the
inherited display and session environment. Do not edit dependency checkouts.
