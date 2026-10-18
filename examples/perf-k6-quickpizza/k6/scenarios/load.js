// load_constant_arrival_p95_under_slo: the expected peak as two open-model scenarios
// running side by side, 4 menu reads for every recommendation (BROWSE_RATE and
// ORDER_RATE per second, for DURATION). Requests keep arriving on schedule when
// QuickPizza slows down, so slowness shows up as latency, not as a lower rate.
// Refuses a non-local target (lib/guard.js); run it against quickpizza-local.

import exec from 'k6/execution';
import { targetFor } from '../lib/config.js';
import { PATHS, RESTRICTIONS } from '../lib/data.js';
import { browse, recommend } from '../lib/quickpizza.js';
import { summaryFor } from '../lib/summary.js';
import { thresholdsFor, TREND_STATS } from '../thresholds.js';

const TARGET = targetFor('load');
const DURATION = __ENV.DURATION || '1m';
const THRESHOLDS = thresholdsFor('load');

export const options = {
  scenarios: {
    browse: {
      executor: 'constant-arrival-rate',
      exec: 'browseMenu',
      rate: Number(__ENV.BROWSE_RATE || 40),
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 20,
      maxVUs: 100,
    },
    order: {
      executor: 'constant-arrival-rate',
      exec: 'orderPizza',
      rate: Number(__ENV.ORDER_RATE || 10),
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 10,
      maxVUs: 50,
    },
  },
  thresholds: {
    ...THRESHOLDS,
    // A dropped iteration means the generator could not hold the rate: the run did not
    // apply the load it claims, so it cannot pass.
    dropped_iterations: ['count<1'],
    // The recommendation is the expensive call; give it its own line in the verdict.
    'http_req_duration{name:recommend}': THRESHOLDS.http_req_duration,
  },
  summaryTrendStats: TREND_STATS,
};

export function browseMenu() {
  browse(TARGET, PATHS[exec.scenario.iterationInTest % PATHS.length]);
}

export function orderPizza() {
  const restriction = RESTRICTIONS[exec.scenario.iterationInTest % RESTRICTIONS.length];
  recommend(TARGET, restriction, exec.vu.idInTest);
}

export const handleSummary = summaryFor('load');
