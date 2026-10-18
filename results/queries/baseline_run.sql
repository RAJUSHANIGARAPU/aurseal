-- The previous finished run on the same project, lane and branch.
SELECT b.run_id FROM runs b, runs x
WHERE x.run_id = :run_id
  AND b.project_id = x.project_id
  AND b.run_id <> x.run_id
  AND b.status = 'finished'
  AND COALESCE(b.lane, '') = COALESCE(x.lane, '')
  AND COALESCE(b.git_branch, '') = COALESCE(x.git_branch, '')
  AND (b.started_at < x.started_at OR (b.started_at = x.started_at AND b.id < x.id))
ORDER BY b.started_at DESC, b.id DESC
LIMIT 1
