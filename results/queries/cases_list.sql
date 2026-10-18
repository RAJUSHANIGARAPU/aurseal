-- Catalogue cases; every filter is optional. `marker` matches a marker name.
SELECT c.* FROM cases c
WHERE c.project_id = COALESCE(:project_id, 'default')
  AND (:status IS NULL OR c.status = :status)
  AND (:owner IS NULL OR c.owner = :owner)
  AND (:priority IS NULL OR c.priority = :priority)
  AND (:marker IS NULL OR c.markers_json LIKE '%"name":"' || :marker || '"%')
  AND (:requirement IS NULL OR EXISTS (
        SELECT 1 FROM case_links l
        WHERE l.project_id = c.project_id AND l.case_id = c.case_id
          AND l.kind = 'requirement' AND l.ref = :requirement))
  AND (:feature IS NULL OR EXISTS (
        SELECT 1 FROM case_links l
        WHERE l.project_id = c.project_id AND l.case_id = c.case_id
          AND l.kind = 'feature' AND l.ref = :feature))
ORDER BY c.case_key
LIMIT COALESCE(:limit, 1000) OFFSET COALESCE(:offset, 0)
