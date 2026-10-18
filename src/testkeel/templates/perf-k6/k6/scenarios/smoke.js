// Smoke: one VU for 30 s requests every path in data/paths.csv, then pauses a second.
// It proves the target is up and every path answers inside the smoke budget. It is the
// only profile that may point at a host you do not own.

import http from 'k6/http';
import { sleep } from 'k6';
import { expectStatus } from '../lib/checks.js';
import { PARAMS, targetFor } from '../lib/config.js';
import { PATHS } from '../lib/data.js';
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
    const response = http.get(`${TARGET}${row.path}`, { ...PARAMS, tags: { name: row.name } });
    expectStatus(response, row.expected_status, row.name);
  }
  // Think time keeps one VU to about one pass per second: polite on a shared host.
  sleep(1);
}

export const handleSummary = summaryFor('smoke');
