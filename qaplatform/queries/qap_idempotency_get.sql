-- A stored response for an Idempotency-Key in one project.
SELECT k.* FROM qap_idempotency_keys k WHERE k.project_id = :project_id AND k.key = :key
