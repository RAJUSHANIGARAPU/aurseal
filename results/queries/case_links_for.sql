-- Links of one case.
SELECT kind, ref, source FROM case_links
WHERE case_id = :case_id AND project_id = COALESCE(:project_id, 'default')
ORDER BY kind, ref
