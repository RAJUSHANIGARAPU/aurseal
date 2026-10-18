-- Invocations of one run, in order.
SELECT * FROM invocations WHERE run_id = :run_id ORDER BY seq
