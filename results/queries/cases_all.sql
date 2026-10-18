-- Every case of every project, with its links flattened.
SELECT c.*, (SELECT group_concat(l.kind || ':' || l.ref, ' ') FROM case_links l
             WHERE l.project_id = c.project_id AND l.case_id = c.case_id) AS links_text,
       cr.steps_json
FROM cases c
LEFT JOIN case_revisions cr
  ON cr.project_id = c.project_id AND cr.case_id = c.case_id
 AND cr.revision_id = c.current_revision_id
ORDER BY c.project_id, c.case_key
