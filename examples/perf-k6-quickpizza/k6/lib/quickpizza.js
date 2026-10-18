// The two things a QuickPizza visitor does: read the menu pages, and ask for a pizza
// recommendation. Every profile composes these; none of them builds a request itself.

import http from 'k6/http';
import { check } from 'k6';
import { expectStatus } from './checks.js';
import { PARAMS } from './config.js';

// QuickPizza's demo endpoints accept any 16-character token (observed on
// quickpizza-local 0.15.31). One token per VU makes each VU a distinct customer.
function tokenFor(vu) {
  return `perf${String(vu).padStart(12, '0')}`;
}

export function browse(target, row) {
  const response = http.get(`${target}${row.path}`, { ...PARAMS, tags: { name: row.name } });
  expectStatus(response, row.expected_status, row.name);
  return response;
}

export function recommend(target, restriction, vu) {
  const maxCalories = Number(restriction.max_calories_per_slice);
  const vegetarian = restriction.must_be_vegetarian === 'true';
  const body = JSON.stringify({
    maxCaloriesPerSlice: maxCalories,
    mustBeVegetarian: vegetarian,
    excludedIngredients: [],
    excludedTools: [],
    minNumberOfToppings: Number(restriction.min_toppings),
    maxNumberOfToppings: Number(restriction.max_toppings),
  });
  const response = http.post(`${target}/api/pizza`, body, {
    ...PARAMS,
    headers: {
      ...PARAMS.headers,
      'Content-Type': 'application/json',
      Authorization: `token ${tokenFor(vu)}`,
    },
    tags: { name: 'recommend', restriction: restriction.name },
  });
  // A fast wrong answer is still wrong: these checks show it in the summary.
  if (expectStatus(response, 200, 'recommend')) {
    check(response, {
      'recommend: calories within the limit': (r) => r.json('calories') <= maxCalories,
      'recommend: vegetarian when asked': (r) => !vegetarian || r.json('vegetarian') === true,
    });
  }
  return response;
}
