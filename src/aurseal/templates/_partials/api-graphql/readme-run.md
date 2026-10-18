```bash
make test        # installs dependencies on first run, then runs the suite in parallel
```

Prerequisites: [uv](https://docs.astral.sh/uv/) and `make`.{% if docker %} Or, with only Docker:
`docker compose run --rm tests`.{% endif %} Expected runtime: under a minute.

`make test-offline` runs everything that needs no network, including the check that every
operation is valid against `schema.graphql`. Point the live tests at another endpoint with
`APP_API_BASE_URL=https://... make test` (every setting: [.env.example](.env.example)).

Record the schema snapshot from the endpoint under test once, and again whenever the change
is intended: `PYTHONPATH=src uv run python -m {{ package_name }}.contract --record`.
