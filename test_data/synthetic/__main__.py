"""``python -m test_data.synthetic generate|mask ...`` — the generator from a shell.

    python -m test_data.synthetic generate --profile test_data/profiles/customer.json \
        --mode boundary --n 50 --seed 7
    SYNTHETIC_MASK_KEY=... python -m test_data.synthetic mask --rules rules.json < in.jsonl

Both write one JSON object per line to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from test_data.synthetic.generator import MODES, SyntheticGenerator
from test_data.synthetic.mask import mask_key, mask_record
from test_data.synthetic.profile import Profile


def main(argv: Sequence[str] | None = None, stdin=None, out=None) -> int:
    stdin = stdin or sys.stdin
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m test_data.synthetic")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="generate records from a profile")
    gen.add_argument("--profile", required=True)
    gen.add_argument("--mode", choices=MODES, default="valid")
    gen.add_argument("--n", type=int, default=10)
    gen.add_argument("--seed", type=int, default=0)
    msk = sub.add_parser("mask", help="mask JSON records read from stdin")
    msk.add_argument("--rules", required=True, help="JSON file {field: rule}")
    args = parser.parse_args(argv)

    if args.command == "generate":
        profile = Profile.from_json(args.profile)
        for record in SyntheticGenerator(args.seed).generate(profile, args.n, args.mode):
            print(json.dumps(record.to_dict(), ensure_ascii=False), file=out)
        return 0

    with open(args.rules, encoding="utf-8") as handle:
        rules = json.load(handle)
    key = mask_key()
    for line in stdin:
        if line.strip():
            print(json.dumps(mask_record(json.loads(line), rules, key)), file=out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
