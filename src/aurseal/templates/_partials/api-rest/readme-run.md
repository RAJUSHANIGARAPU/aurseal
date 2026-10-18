```bash
make test        # installs dependencies on first run, then runs the suite in parallel
```

Prerequisites: [uv](https://docs.astral.sh/uv/) and `make`.{% if docker %} Or, with only Docker:
`docker compose run --rm tests`.{% endif %} Expected runtime: under a minute.

`make test-offline` runs everything that needs no network. Point the live tests at another
environment with `APP_API_BASE_URL=https://... make test` (every setting:
[.env.example](.env.example)).
