```bash
make test        # installs dependencies and Chromium on first run, then runs the suite in parallel
```

Prerequisites: [uv](https://docs.astral.sh/uv/) and `make`.{% if docker %} Or, with only Docker:
`docker compose run --rm tests`.{% endif %} Expected runtime: about a minute on a laptop.

Point the suite at another environment with `APP_BASE_URL=https://... make test`
(every setting: [.env.example](.env.example)).
