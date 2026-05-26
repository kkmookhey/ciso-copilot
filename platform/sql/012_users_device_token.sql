-- 012_users_device_token.sql — add device_token to users
-- Spec'd in CISOBrief-v2.md §8 line 302 but never landed; required by the
-- soc-s1 push code in platform/lambda/event_router/main.py. Single-token-
-- per-user for v1 (multi-device support deferred per v2 PRD's own note).
ALTER TABLE users ADD COLUMN IF NOT EXISTS device_token TEXT;
