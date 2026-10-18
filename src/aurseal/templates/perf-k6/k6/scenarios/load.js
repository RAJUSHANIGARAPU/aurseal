// Load: a constant arrival rate (RATE iterations per second for DURATION), the expected
// peak. An open model: new iterations start on schedule even when the target slows down,
// so latency shows up in the numbers instead of quietly lowering the request rate.
// Refuses a non-local target (lib/guard.js).

import http from 'k6/http';
import exec from 'k6/execution';
import { expectStatus } from '../lib/checks.js';
import { PARAMS, targetFor } from '../lib/config.js';
import { PATHS } from '../lib/data.js';
import { summaryFor } from '../lib/summary.js';
import { thresholdsFor, TREND_STATS } from '../thresholds.js';

const TARGET = targetFor('load');

export const options = {
  scenarios: {
    load: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.RATE || 20),
      timeUnit: '1s',
      duration: __ENV.DURATION || '1m',
      preAllocatedVUs: Number(__ENV.VUS || 20),
      maxVUs: Number(__ENV.MAX_VUS || 100),
    },
  },
  thresholds: {
    ...thresholdsFor('load'),
    // A dropped iteration means the generator could not hold the rate: the run did not
    // apply the load it claims, so it cannot pass.
    dropped_iterations: ['count<1'],
  },
  summaryTrendStats: TREND_STATS,
};

export default function () {
  // Round-robin over the paths: every path gets its share, run after run.
  const row = PATHS[exec.scenario.iterationInTest % PATHS.length];
  const response = http.get(`${TARGET}${row.path}`, { ...PARAMS, tags: { name: row.name } });
  expectStatus(response, row.expected_status, row.name);
}

export const handleSummary = summaryFor('load');
