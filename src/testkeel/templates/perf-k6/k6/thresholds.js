// Service level objectives in one place. The budgets live in slo.json, which the offline
// Python tests validate; this module turns them into k6 thresholds. A breached threshold
// makes k6 exit non-zero, and that exit code is what fails `make` and CI.
//
// Checks (lib/checks.js) never decide pass or fail: they say which request went wrong.

const SLO = JSON.parse(open('./slo.json'));

export const TREND_STATS = ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'];

export function thresholdsFor(profile) {
  const budget = SLO[profile];
  if (!budget) {
    throw new Error(`slo.json has no budget for the ${profile} profile`);
  }
  // Stress stops as soon as the error budget is spent: past that point it measures a
  // broken system. delayAbortEval lets the first seconds of warm-up settle first.
  const abort = budget.abort_on_fail
    ? { abortOnFail: true, delayAbortEval: budget.delay_abort_eval || '10s' }
    : {};
  return {
    http_req_duration: [`p(95)<${budget.p95_ms}`],
    http_req_failed: [{ threshold: `rate<${budget.error_rate}`, ...abort }],
  };
}
