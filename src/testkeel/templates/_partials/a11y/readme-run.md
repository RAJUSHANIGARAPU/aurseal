```bash
make test        # installs dependencies and Chromium on first run, then runs the suite in parallel
```

Prerequisites: [uv](https://docs.astral.sh/uv/) and `make`.{% if docker %} Or, with only Docker:
`docker compose run --rm tests`.{% endif %} Expected runtime: about a minute on a laptop.

`make test-offline` scans the local HTML fixtures only: it proves the scanner finds what it
should before any live result is trusted. Point the suite at another site with
`APP_BASE_URL=https://... make test`; scan for WCAG 2.2 too with
`APP_WCAG_TAGS=wcag2a,wcag2aa,wcag21a,wcag21aa,wcag22aa` (every setting:
[.env.example](.env.example)).
