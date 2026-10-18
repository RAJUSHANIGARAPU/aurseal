-- One case, with its current revision's steps.
SELECT c.*, cr.steps_json
FROM cases c
LEFT JOIN case_revisions cr
  ON cr.project_id = c.project_id AND cr.case_id = c.case_id
 AND cr.revision_id = c.current_revision_id
WHERE c.case_id = :case_id AND c.project_id = COALESCE(:project_id, 'default')
