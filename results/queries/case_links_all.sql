-- Every case link of a project.
SELECT case_id, kind, ref, source FROM case_links
WHERE project_id = COALESCE(:project_id, 'default')
ORDER BY case_id, kind, ref
