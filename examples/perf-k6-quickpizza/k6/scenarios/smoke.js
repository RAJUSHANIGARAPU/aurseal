// smoke_1vu_30s_all_endpoints_respond: one VU for 30 s reads every menu page and asks
// for one recommendation per restriction, then pauses a second. It proves QuickPizza is
// up and answers inside the smoke budget. It is the only profile that may point at the
// public instance (https://quickpizza.grafana.com).

import exec from 'k6/execution';
import { sleep } from 'k6';
import { targetFor } from '../lib/config.js';
import { PATHS, RESTRICTIONS } from '../lib/data.js';
import { browse, recommend } from '../lib/quickpizza.js';
import { summaryFor } from '../lib/summary.js';
import { thresholdsFor, TREND_STATS } from '../thresholds.js';

const TARGET = targetFor('smoke');

export const options = {
  scenarios: {
    smoke: { executor: 'constant-vus', vus: 1, duration: __ENV.DURATION || '30s' },
  },
  thresholds: thresholdsFor('smoke'),
  summaryTrendStats: TREND_STATS,
};

export default function () {
  for (const row of PATHS) {
    browse(TARGET, row);
  }
  for (const restriction of RESTRICTIONS) {
    recommend(TARGET, restriction, exec.vu.idInTest);
  }
  // Think time keeps one VU to about one pass per second: polite on a shared host.
  sleep(1);
}

export const handleSummary = summaryFor('smoke');
