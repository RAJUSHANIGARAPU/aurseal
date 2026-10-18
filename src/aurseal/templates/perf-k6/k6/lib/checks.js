// Checks record which request misbehaved; they show up in the summary but never fail
// the run. Pass or fail is the thresholds' job (thresholds.js), so a failing check is
// a clue to read, and a failing threshold is the verdict.

import { check } from 'k6';

export function expectStatus(response, expected, name) {
  return check(response, {
    [`${name}: status is ${expected}`]: (r) => r.status === Number(expected),
  });
}
