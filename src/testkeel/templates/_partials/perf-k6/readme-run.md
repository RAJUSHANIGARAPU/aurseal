```bash
make smoke       # 1 VU for 30 s against {{ target_url }}; fails when an SLO is breached
make test        # the offline checks, then the smoke profile
```

Prerequisites: [k6](https://grafana.com/docs/k6/latest/set-up/install-k6/), [uv](https://docs.astral.sh/uv/)
and `make`.{% if docker %} Or, with only Docker: `make docker-smoke`.{% endif %} Expected runtime:
under a minute.

`make load` and `make stress` put real load on the target, so they refuse anything that is
not on this machine. Run the system under test locally and point at it:
`make load APP_TARGET_URL=http://localhost:3333`. `ALLOW_REMOTE_LOAD=1` lifts the refusal;
set it only with the host owner's consent. Every setting: [.env.example](.env.example).
