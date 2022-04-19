WITH w AS (
  UPDATE workspaces SET name = $2
  WHERE workspaces.id = $1
  RETURNING workspaces.*
),
u AS (
  SELECT username FROM users, w
  WHERE users.id = w.user_id
),
p AS (
  SELECT COUNT(*) AS num_projects
  FROM projects
  WHERE workspace_id = $1
)
SELECT w.id, w.name, w.archived, w.immutable,
  u.username, p.num_projects
FROM w, u, p;
