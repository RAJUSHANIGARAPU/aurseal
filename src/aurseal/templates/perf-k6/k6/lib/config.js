// Where a run points and how its requests identify themselves.

import { assertTargetAllowed } from './guard.js';

if (!__ENV.TARGET_URL) {
  throw new Error('set TARGET_URL, e.g. k6 run -e TARGET_URL=http://localhost:3333 <scenario>');
}

export const TARGET_URL = __ENV.TARGET_URL.replace(/\/+$/, '');

// A stable User-Agent lets the owner of the target find (and filter) this traffic.
export const PARAMS = {
  headers: { 'User-Agent': 'k6-perf-suite' },
  timeout: __ENV.REQUEST_TIMEOUT || '10s',
};

export function targetFor(profile) {
  assertTargetAllowed(profile, TARGET_URL);
  return TARGET_URL;
}
