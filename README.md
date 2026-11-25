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

![tui screenshot](docs/assets/tui.png)

## Using the Web Interface

The first web version displays `omniline web` after login. Messaging routes
will be added later. Flask, Gunicorn, and the required Apache proxy modules
are already declared or installed as follows:

- Python dependencies: `flask` and `gunicorn` (installed by `uv sync` or
  `pip install omniline`)
- Debian package: `apache2` (only needed for the Apache deployment)
- Apache modules: `proxy`, `proxy_http`, and `headers`

### Initialize web authentication

Create the password verifier and Flask session-signing secret interactively:

```
omniline web --init-auth
```

This creates:

```
~/.omniline/password.hash
~/.omniline/session.secret
```

The password is never stored in clear text. Both files are created with
restrictive permissions and must remain private. Authentication is enabled by
default and is not disabled by the HTTP/HTTPS configuration.

### Local HTTP development server

Run the Flask development server on loopback:

```
omniline web
```

Open `http://127.0.0.1:1730/`. This server is for development only. For plain
HTTP development, do not set `OMNILINE_COOKIE_SECURE`.

### Gunicorn

Run Gunicorn on a loopback address:

```
uv run gunicorn \
    --bind 127.0.0.1:8001 \
    omniline.web:app
```

For an HTTPS deployment behind Apache, set the cookie transport flag:

```
OMNILINE_COOKIE_SECURE=1 \
uv run gunicorn \
    --bind 127.0.0.1:8001 \
    omniline.web:app
```

`OMNILINE_COOKIE_SECURE` controls only the browser's `Secure` cookie
attribute. It does not bypass password checking. Password checking always
occurs; there is no authentication-bypass environment variable.

### Apache2 path-specific reverse proxy

If Apache already serves other pages, add the following inside the existing
HTTPS `<VirtualHost>` block. It makes only `/omniline/` reach Gunicorn:

```
RedirectMatch 301 ^/omniline$ /omniline/

ProxyPass        /omniline/ http://127.0.0.1:8001/
ProxyPassReverse /omniline/ http://127.0.0.1:8001/

<Location /omniline/>
    RequestHeader set X-Forwarded-Prefix "/omniline"
    Require all granted
</Location>

ProxyPassReverseCookiePath / /omniline/
```

Place these rules before a generic `ProxyPass / ...` rule, if one exists.
`X-Forwarded-Prefix` allows Flask to generate redirects such as
`/omniline/login` rather than `/login`. `ProxyPassReverseCookiePath` keeps the
authentication cookie scoped to OmniLine.

Enable the modules and validate the configuration:

```
sudo a2enmod proxy proxy_http headers
sudo apache2ctl configtest
sudo apache2ctl -k graceful
```

On systems without `systemctl`, use `apache2ctl -k start`, `-k graceful`, and
`-k stop`. Start Gunicorn before testing the Apache proxy. The public URL is:

```
https://your-host/omniline/
```

Gunicorn should remain bound to `127.0.0.1`; Apache is the public TLS and
reverse-proxy boundary.

## License

[MIT](LICENSE)
