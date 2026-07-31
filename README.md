# Afterimage

Afterimage is a Flask application for sharing an image through a short, single-view session. The server splits each image into a grid and delivers the fragments once, in order, through an IP-bound session. The browser then renders fast randomized combinations on a canvas without receiving a public URL for the original file.

The interface automatically supports English and French using the browser's `Accept-Language` preference, with a persistent manual language selector.

## Features

- One viewing session per image and HMAC-hashed IP address
- Original uploads are never exposed through a public static route
- 30-fragment (6×5) or 60-fragment (10×6) grids
- Randomized frames showing at most one third of the fragments
- Single-use, ordered fragment delivery with short-lived bearer tokens
- `no-store` caching rules and restrictive browser security headers
- Mandatory viewer pledge before a session can start
- English/French localization
- Responsive desktop, tablet, and mobile interface
- Protected administrative statistics endpoint

## Important limitation

This application makes casual screenshots and direct downloads harder; it cannot make image capture impossible. A modified browser can collect fragments, screen-recording software can observe rendered frames, and an external camera can always film a visible display. Do not use Afterimage as the sole protection for highly sensitive material.

## Requirements

- Python 3.12 or 3.13
- Flask
- Pillow

## Local setup

```bash
git clone <your-repository-url>
cd limitScreenshot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export IP_HASH_SECRET="$(openssl rand -hex 32)"
export ADMIN_TOKEN="$(openssl rand -hex 32)"
python app.py
```

Open <http://127.0.0.1:5000>.

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1` and set environment variables with `$env:IP_HASH_SECRET = "..."`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `IP_HASH_SECRET` | Locally generated `.ip_hash_secret` | Stable secret used to HMAC viewer IP addresses. Set explicitly in production. |
| `ADMIN_TOKEN` | None | Bearer token required by `/stats`; the route returns 404 when unset. |
| `TRUSTED_PROXY_COUNT` | `0` | Number of trusted reverse proxies supplying forwarding headers. Never enable this without a controlled proxy. |
| `DATABASE_PATH` | `./data.db` | SQLite database location. |
| `UPLOAD_FOLDER` | `./uploads` | Private upload storage location. |
| `FLASK_DEBUG` | `0` | Enables Flask debug mode only when set to `1`. Never enable in production. |

`.env.example` is a reference file; the app does not automatically load it. Export the variables through your shell, container runtime, or service manager.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

GitHub Actions runs the suite against Python 3.12 and 3.13 on every push and pull request.

## Production notes

- Serve the app through HTTPS and a production WSGI server.
- Keep `IP_HASH_SECRET` stable. Changing it invalidates the relationship with existing IP blocks.
- Store the SQLite database and uploads on persistent private storage.
- Set `TRUSTED_PROXY_COUNT` to the exact proxy count only when forwarding headers are sanitized by infrastructure you control.
- Add authentication and rate limiting to `/upload` before exposing public uploads.
- Back up data according to the sensitivity and retention policy of your deployment.

Administrative statistics can be requested with:

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" https://example.com/stats
```

## Project structure

```text
app.py                 Flask routes, storage, sessions, and image fragmentation
translations.py        English and French interface strings
templates/             Jinja templates
static/css/             Responsive interface styles
static/js/              Upload and fragment-viewer behavior
tests/                  Automated application tests
.github/workflows/      Continuous integration
```

## License

No license has been selected yet. Until a license is added, copyright law reserves all rights to the repository owner.
