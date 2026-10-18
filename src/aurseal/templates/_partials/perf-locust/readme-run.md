```bash
make smoke       # 5 users for 30 s against {{ target_url }}, then the SLO check
make test        # the offline checks, then the smoke profile
```

Prerequisites: [uv](https://docs.astral.sh/uv/) and `make`.{% if docker %} Or, with only Docker:
`make docker-load` (one master, two workers, the smoke profile).{% endif %} Expected runtime:
under a minute.

`make load` and `make stress` put real load on the target, so they refuse anything that is
not on this machine. Run the system under test locally and point at it:
`make load APP_TARGET_URL=http://localhost:3333`. `ALLOW_REMOTE_LOAD=1` lifts the refusal;
set it only with the host owner's consent. Every setting: [.env.example](.env.example).
