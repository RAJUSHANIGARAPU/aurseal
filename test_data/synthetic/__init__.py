"""Synthetic and masked test data: constraint profiles, seeded generation, masking, probing.

    profile = Profile.from_json("test_data/profiles/customer.json")
    records = SyntheticGenerator(seed=7).generate(profile, 20, mode="invalid")
    results = probe("/api/customers", http, records)

See ``docs/features/synthetic-data.md``.
"""

from test_data.synthetic.generator import MODES, SyntheticGenerator, SyntheticRecord
from test_data.synthetic.mask import mask_record
from test_data.synthetic.params import SyntheticParams
from test_data.synthetic.probe import ProbeResult, probe
from test_data.synthetic.profile import FieldSpec, Profile

__all__ = [
    "MODES",
    "FieldSpec",
    "ProbeResult",
    "Profile",
    "SyntheticGenerator",
    "SyntheticParams",
    "SyntheticRecord",
    "mask_record",
    "probe",
]
