-- Defect links with the titles of the cases they hang on.
SELECT l.project_id, l.ref, group_concat(COALESCE(c.title, c.case_key), ' | ') AS cases
FROM case_links l
JOIN cases c ON c.project_id = l.project_id AND c.case_id = l.case_id
WHERE l.kind = 'defect'
GROUP BY l.project_id, l.ref
ORDER BY l.ref
