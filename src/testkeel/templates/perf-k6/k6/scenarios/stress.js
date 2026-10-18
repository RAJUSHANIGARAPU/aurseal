// Stress: the arrival rate ramps past the expected peak until the error budget is spent.
// The http_req_failed threshold aborts the run at that point (abortOnFail in
// thresholds.js), so a failed stress run is the finding: the rate it reached is the
// capacity limit. Refuses a non-local target (lib/guard.js).

import http from 'k6/http';
import exec from 'k6/execution';
import { expectStatus } from '../lib/checks.js';
import { PARAMS, targetFor } from '../lib/config.js';
import { PATHS } from '../lib/data.js';
import { summaryFor } from '../lib/summary.js';
import { thresholdsFor, TREND_STATS } from '../thresholds.js';

const TARGET = targetFor('stress');
const PEAK = Number(__ENV.PEAK_RATE || 200);

export const options = {
  scenarios: {
    stress: {
      executor: 'ramping-arrival-rate',
      startRate: Math.max(1, Math.round(PEAK / 10)),
      timeUnit: '1s',
      preAllocatedVUs: Number(__ENV.VUS || 50),
      maxVUs: Number(__ENV.MAX_VUS || 400),
      stages: [
        { duration: '30s', target: Math.round(PEAK / 4) },
        { duration: '30s', target: Math.round(PEAK / 2) },
        { duration: '30s', target: PEAK },
        { duration: '30s', target: PEAK },
        { duration: '10s', target: 0 },
      ],
    },
  },
  thresholds: thresholdsFor('stress'),
  summaryTrendStats: TREND_STATS,
};

export default function () {
  const row = PATHS[exec.scenario.iterationInTest % PATHS.length];
  const response = http.get(`${TARGET}${row.path}`, { ...PARAMS, tags: { name: row.name } });
  expectStatus(response, row.expected_status, row.name);
}

export const handleSummary = summaryFor('stress');
