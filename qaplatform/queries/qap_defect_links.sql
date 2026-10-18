-- Defect links of one project's cases.
SELECT l.case_id, l.ref AS ticket FROM case_links l
WHERE l.project_id = :project_id AND l.kind = 'defect'
ORDER BY l.ref
