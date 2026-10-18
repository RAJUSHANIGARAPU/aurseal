// Test data, read once in the init context and shared by every VU (SharedArray keeps a
// single copy instead of one per VU).

import { SharedArray } from 'k6/data';

function parseCsv(text) {
  const [header, ...rows] = text.trim().split(/\r?\n/);
  const keys = header.split(',').map((key) => key.trim());
  return rows
    .filter((row) => row.trim() && !row.startsWith('#'))
    .map((row) => {
      const cells = row.split(',');
      return Object.fromEntries(keys.map((key, i) => [key, (cells[i] || '').trim()]));
    });
}

// One row per request a profile makes: name (the metric tag), path, expected status.
export const PATHS = new SharedArray('paths', () => parseCsv(open('../data/paths.csv')));
