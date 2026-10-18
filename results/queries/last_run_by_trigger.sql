-- Runs started by one trigger (e.g. schedule), newest first; schedule_name is optional.
SELECT r.run_id, r.started_at, r.trigger, r.schedule_name
FROM runs r
WHERE r.trigger = :trigger
  AND (:schedule_name IS NULL OR r.schedule_name = :schedule_name)
  AND (:project_id IS NULL OR r.project_id = :project_id)
ORDER BY r.started_at DESC, r.id DESC
LIMIT COALESCE(:limit, 20)
