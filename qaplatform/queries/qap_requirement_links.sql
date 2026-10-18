-- Requirement links of one project's cases.
SELECT l.case_id, l.ref AS requirement, c.case_key
FROM case_links l
JOIN cases c ON c.project_id = l.project_id AND c.case_id = l.case_id
WHERE l.project_id = :project_id AND l.kind = 'requirement'
  AND (:requirement IS NULL OR l.ref = :requirement)
ORDER BY l.ref, c.case_key
