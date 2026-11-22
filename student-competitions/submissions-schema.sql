-- 学生作品提交表（/sc 提交页写入；审核通过后展示在作品集页面）
CREATE TABLE IF NOT EXISTS submissions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  uid         TEXT NOT NULL UNIQUE,          -- 对外展示的短编号
  team        TEXT NOT NULL,
  school      TEXT NOT NULL,
  competition TEXT NOT NULL,
  track       TEXT NOT NULL DEFAULT '',
  title       TEXT NOT NULL,
  url         TEXT NOT NULL,
  description TEXT NOT NULL,
  members     TEXT NOT NULL DEFAULT '',
  contact     TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected
  ip_hash     TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  reviewed_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_submissions_ip ON submissions(ip_hash, created_at DESC);
