# OmniLine

OmniLine is a set of three frontends for LINE, a popular messaging app in Taiwan. OmniLine provides the user interfaces; authentication and communication are provided by [OkLine](https://nicedayzc.github.io/OkLine/), created by NiceDayZc.

The three frontends of OmniLine are:
- **omniline cli**: a bare-bones command-line frontend, mostly for testing
- **omniline tui**: a terminal user interface for reading and sending messages
- **omniline web**: to be written

## Installation and Initialization

Create and activate a Python virtual environment, using either `pip` or `uv`. Then:
```
uv pip install omniline
omniline init
```
or
```
pip install omniline
omniline init
```
`omniline init` creates `~/.omniline/` and copies the default configuration and themes. Existing files are preserved; use `omniline init --force` to replace them. The virtual environment may be located elsewhere.

## Logging In

1. Have your main LINE device at hand
2. Log in to your LINE account from the Python virtual environment: `okline login`
3. A QR code will be printed. On your main LINE device, navigate to "add contact" => "scan QR code", and scan the QR code.
4. The main device will ask for a 6-digit pin. Read it from the command line, enter it into the main device, and confirm login.
5. A tokens.json file will be generated, possibly overwriting existing one. This is your login credential. Keep it secret. All future omniline commands need to read it.

![okline login process](docs/assets/login.png)

## Using the TUI

Start the TUI with `omniline tui`.

The full-screen layout has a chat list on the left and the selected chat or
profile on the right. The status bar shows the currently available keyboard
commands.

- List mode: navigate chats, search, open a chat, open a profile, refresh unread
  counts, and quit.
- Profile mode: view chat information, pin or unpin the chat, and return.
- Content mode: navigate and search messages, move between search matches, open
  the first URL on the current line, compose a new message or reply, and return.
- Search and compose overlays: enter text, submit it, or cancel with the shown
  keys. Sending follows the configured confirmation and cursor-line safeguards.

Important settings are in `~/.omniline/config.toml`: the token-file path, shared
pinned chats, history limit, TUI theme, and conservative sending options. Dark
and light themes are stored under `~/.omniline/themes/`.

## License

[MIT](LICENSE)
