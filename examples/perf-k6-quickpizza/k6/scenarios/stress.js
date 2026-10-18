// stress_ramp_until_error_rate_breaches_1pct: the recommendation rate ramps past the
// load profile's peak (to PEAK_RATE per second) while menu reads continue at a quarter
// of it. The http_req_failed threshold aborts the run once more than 1 % of requests
// fail (abortOnFail in thresholds.js), so a failed stress run is the finding: the rate it
// reached is QuickPizza's capacity limit on this machine.
// Refuses a non-local target (lib/guard.js); run it against quickpizza-local.

import exec from 'k6/execution';
import { targetFor } from '../lib/config.js';
import { PATHS, RESTRICTIONS } from '../lib/data.js';
import { browse, recommend } from '../lib/quickpizza.js';
import { summaryFor } from '../lib/summary.js';
import { thresholdsFor, TREND_STATS } from '../thresholds.js';

const TARGET = targetFor('stress');
const PEAK = Number(__ENV.PEAK_RATE || 400);

const ramp = (peak) => [
  { duration: '30s', target: Math.round(peak / 4) },
  { duration: '30s', target: Math.round(peak / 2) },
  { duration: '30s', target: peak },
  { duration: '30s', target: peak },
  { duration: '10s', target: 0 },
];

export const options = {
  scenarios: {
    order: {
      executor: 'ramping-arrival-rate',
      exec: 'orderPizza',
      startRate: 10,
      timeUnit: '1s',
      preAllocatedVUs: 50,
      maxVUs: 500,
      stages: ramp(PEAK),
    },
    browse: {
      executor: 'ramping-arrival-rate',
      exec: 'browseMenu',
      startRate: 10,
      timeUnit: '1s',
      preAllocatedVUs: 20,
      maxVUs: 200,
      stages: ramp(Math.round(PEAK / 4)),
    },
  },
  thresholds: thresholdsFor('stress'),
  summaryTrendStats: TREND_STATS,
};

export function browseMenu() {
  browse(TARGET, PATHS[exec.scenario.iterationInTest % PATHS.length]);
}

export function orderPizza() {
  const restriction = RESTRICTIONS[exec.scenario.iterationInTest % RESTRICTIONS.length];
  recommend(TARGET, restriction, exec.vu.idInTest);
}

export const handleSummary = summaryFor('stress');
