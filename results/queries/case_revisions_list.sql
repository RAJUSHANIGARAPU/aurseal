-- Revisions of one case, oldest first, with the run range each one covered.
SELECT cr.*,
    (SELECT tr.run_id FROM test_results tr JOIN runs r ON r.run_id = tr.run_id
     WHERE tr.project_id = cr.project_id AND tr.case_id = cr.case_id
       AND tr.revision_id = cr.revision_id
     ORDER BY r.started_at ASC, r.id ASC LIMIT 1) AS first_run_id,
    (SELECT tr.run_id FROM test_results tr JOIN runs r ON r.run_id = tr.run_id
     WHERE tr.project_id = cr.project_id AND tr.case_id = cr.case_id
       AND tr.revision_id = cr.revision_id
     ORDER BY r.started_at DESC, r.id DESC LIMIT 1) AS last_run_id,
    (SELECT COUNT(DISTINCT tr.run_id) FROM test_results tr
     WHERE tr.project_id = cr.project_id AND tr.case_id = cr.case_id
       AND tr.revision_id = cr.revision_id) AS run_count
FROM case_revisions cr
WHERE cr.case_id = :case_id AND cr.project_id = COALESCE(:project_id, 'default')
ORDER BY cr.created_at, cr.id
