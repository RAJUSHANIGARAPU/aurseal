"""One read-only oracle judged per environment: asserted where the fix runs, a detector elsewhere.

Two demo instances stand for two environments: ``fixed-demo`` runs without the defect and
``unfixed-demo`` has ``reseller_share_scaled_twice`` on (a share percent of 6 is stored as
0.06). The same oracle, read through a ``ReadOnlyClient``, goes through
``verification.fix_scope.judge``: on the environment listed as fixed it is asserted (a pass is
release evidence); on the other it runs as an expected-red detector and reports that the
defect still reproduces.
"""

from __future__ import annotations

import pytest
import requests

from demo_sut.client import DemoClient
from demo_sut.server import DemoServer
from gate.read_only import ReadOnlyClient
from tests.demo_examples.finance import oracles
from verification.evidence_verdict import EvidenceVerdict
from verification.fix_scope import fix_scope_envs, judge
from verification.verdicts import RedVerdict

FIX_ENVS_VARIABLE = "DEMO_RESELLER_FIX_ENVS"
SENT = {"name": "Delta terms", "share_percent": 6, "flat_fee": 1.25}


@pytest.fixture(scope="module")
def environments(demo_sut, tmp_path_factory):
    """``{"fixed-demo": client, "unfixed-demo": client}``; the second demo is started here."""
    second = DemoServer(db_path=tmp_path_factory.mktemp("unfixed") / "demo.sqlite").start()
    unfixed = DemoClient(second.base_url)
    unfixed.replace_faults(["reseller_share_scaled_twice"])
    try:
        yield {"fixed-demo": demo_sut.client, "unfixed-demo": unfixed}
    finally:
        second.stop()


def judge_terms(env, client, terms_id):
    session = requests.Session()
    api = ReadOnlyClient.over(session, client.base_url)
    faults = api.get("/api/health").headers.get("X-Demo-Faults")

    def read():
        response = api.get(f"/api/reseller-terms/{terms_id}")
        assert response.status_code == 200, f"terms {terms_id}: HTTP {response.status_code}"
        return response.json()

    def control():
        # the flat fee is stored as sent everywhere: the read reaches the right record
        assert read()["flat_fee"] == SENT["flat_fee"], read()

    def oracle():
        oracles.assert_share_kept(read(), SENT["share_percent"])

    try:
        return judge(
            env,
            fix_scope_envs(FIX_ENVS_VARIABLE, default="fixed-demo"),
            oracles.SHARE_DEFECT,
            oracles.SHARE_SIGNATURE,
            coverage=EvidenceVerdict(
                f"{oracles.SHARE_DEFECT} share percent kept", ticket=oracles.SHARE_DEFECT
            ),
            control=control,
            oracle=oracle,
            evidence=lambda: f"terms {terms_id} on {env} keep 6 (faults: {faults})",
        )
    finally:
        session.close()


def test_fix_asserted_in_scope_detector_elsewhere(
    environments, demo_control, monkeypatch, record_property
):
    monkeypatch.delenv(FIX_ENVS_VARIABLE, raising=False)
    verdicts = {}
    for env, client in environments.items():
        terms = client.ok("POST", "/api/reseller-terms", SENT)  # set-up, not judged
        record_property(f"{env}_faults", ",".join(client.state()["faults"]) or "none")
        verdicts[env] = judge_terms(env, client, terms["id"])
    assert verdicts == {"fixed-demo": RedVerdict.CLEARED, "unfixed-demo": RedVerdict.REPRODUCES}
