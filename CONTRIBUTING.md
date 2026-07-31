# Contributing

Thank you for helping improve Afterimage.

## Development setup

1. Create and activate a Python virtual environment.
2. Install `requirements-dev.txt`.
3. Set a non-production `IP_HASH_SECRET`.
4. Run `pytest -q` before submitting a change.

Keep runtime databases, secrets, uploaded images, and virtual environments out of Git. Update both English and French entries in `translations.py` whenever user-facing text changes.

## Pull requests

- Keep changes focused and explain their security implications.
- Add or update tests for behavioral changes.
- Preserve the one-session-per-image-and-IP invariant.
- Clearly document limitations rather than claiming screenshot prevention.
- Verify the interface at mobile, tablet, and desktop widths for visual changes.
