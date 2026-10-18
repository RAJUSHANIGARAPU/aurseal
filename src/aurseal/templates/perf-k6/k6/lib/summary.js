// End-of-test summary: the raw data as JSON, a self-contained HTML page, and a short
// text verdict on stdout. Files land in reports/ (the Makefile creates it; k6 does not).

import { TARGET_URL } from './config.js';

const escape = (value) =>
  String(value).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

const number = (value, digits = 2) =>
  typeof value === 'number' ? value.toFixed(digits) : 'n/a';

function thresholdRows(data) {
  const rows = [];
  for (const [metric, body] of Object.entries(data.metrics)) {
    for (const [rule, result] of Object.entries(body.thresholds || {})) {
      rows.push({ metric, rule, ok: result.ok });
    }
  }
  return rows;
}

function checkRows(group, rows = []) {
  for (const item of group.checks || []) {
    rows.push({ name: item.name, passes: item.passes, fails: item.fails });
  }
  for (const child of group.groups || []) {
    checkRows(child, rows);
  }
  return rows;
}

function keyFigures(data) {
  const m = data.metrics;
  const duration = (m.http_req_duration || { values: {} }).values;
  return [
    ['requests', number((m.http_reqs || { values: {} }).values.count, 0)],
    ['requests per second', number((m.http_reqs || { values: {} }).values.rate)],
    ['failed requests', `${number(100 * ((m.http_req_failed || { values: {} }).values.rate || 0))} %`],
    ['duration p50 (ms)', number(duration.med)],
    ['duration p95 (ms)', number(duration['p(95)'])],
    ['duration p99 (ms)', number(duration['p(99)'])],
    ['duration max (ms)', number(duration.max)],
    ['dropped iterations', number((m.dropped_iterations || { values: { count: 0 } }).values.count, 0)],
  ];
}

function html(profile, data) {
  const thresholds = thresholdRows(data);
  const passed = thresholds.every((row) => row.ok);
  const table = (head, rows) =>
    `<table><tr>${head.map((h) => `<th>${escape(h)}</th>`).join('')}</tr>${rows
      .map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join('')}</tr>`)
      .join('')}</table>`;
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>k6 ${escape(profile)}: ${passed ? 'passed' : 'failed'}</title>
<style>body{font:14px system-ui,sans-serif;margin:2rem;max-width:60rem}
table{border-collapse:collapse;margin:1rem 0}td,th{border:1px solid #ccc;padding:.3rem .6rem;text-align:left}
.ok{color:#126b12}.bad{color:#b00020;font-weight:bold}</style></head><body>
<h1>k6 ${escape(profile)} profile: <span class="${passed ? 'ok' : 'bad'}">${passed ? 'passed' : 'failed'}</span></h1>
<p>Target: <code>${escape(TARGET_URL)}</code>. Duration: ${number(data.state.testRunDurationMs / 1000, 1)} s.</p>
<h2>Service level objectives</h2>
${table(['metric', 'threshold', 'result'], thresholds.map((r) => [escape(r.metric), `<code>${escape(r.rule)}</code>`, r.ok ? '<span class="ok">met</span>' : '<span class="bad">breached</span>']))}
<h2>Key figures</h2>
${table(['figure', 'value'], keyFigures(data).map(([k, v]) => [escape(k), escape(v)]))}
<h2>Checks (diagnostic, not the verdict)</h2>
${table(['check', 'passes', 'fails'], checkRows(data.root_group).map((c) => [escape(c.name), c.passes, c.fails ? `<span class="bad">${c.fails}</span>` : 0]))}
</body></html>
`;
}

function text(profile, data) {
  const lines = [`k6 ${profile} against ${TARGET_URL}`];
  for (const [k, v] of keyFigures(data)) {
    lines.push(`  ${k.padEnd(22)} ${v}`);
  }
  for (const row of thresholdRows(data)) {
    lines.push(`  ${row.ok ? 'met     ' : 'BREACHED'} ${row.metric} ${row.rule}`);
  }
  return `${lines.join('\n')}\n`;
}

export function summaryFor(profile) {
  return (data) => ({
    [`reports/k6-${profile}-summary.json`]: JSON.stringify(data, null, 2),
    [`reports/k6-${profile}-summary.html`]: html(profile, data),
    stdout: text(profile, data),
  });
}
