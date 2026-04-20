-- One-time Snowflake customer setup for the Seren family-office catalog.
-- Run this once in the customer's warehouse before enabling Snowflake push
-- on any family-office leaf skill. Once FO_ARTIFACTS exists, all 55 leaves
-- can write to it — this file ships alongside cpa-tax-package-checklist as
-- the canonical reference.
--
-- Credential principle: whatever role the skill authenticates as must have
-- INSERT privilege on FO_ARTIFACTS in the target schema — nothing more.

-- 1. Target database/schema (edit for your warehouse layout):
--    USE DATABASE FO_DB;
--    USE SCHEMA SEREN;

-- 2. The sink table. Idempotent.
CREATE TABLE IF NOT EXISTS FO_ARTIFACTS (
  artifact_id        STRING      NOT NULL,
  pillar             STRING      NOT NULL,
  skill_name         STRING      NOT NULL,
  artifact_name      STRING      NOT NULL,
  artifact_version   INTEGER     NOT NULL,
  created_at         TIMESTAMP_TZ NOT NULL,
  created_by         STRING      NOT NULL,
  content_hash       STRING      NOT NULL,
  structured_payload VARIANT,
  PRIMARY KEY (artifact_id)
);

-- 3. A least-privilege write role the skill can authenticate as.
--    Replace FO_WRITER and the target schema path to match your warehouse.
-- CREATE ROLE IF NOT EXISTS FO_WRITER;
-- GRANT USAGE ON WAREHOUSE FO_WH TO ROLE FO_WRITER;
-- GRANT USAGE ON DATABASE FO_DB TO ROLE FO_WRITER;
-- GRANT USAGE ON SCHEMA FO_DB.SEREN TO ROLE FO_WRITER;
-- GRANT INSERT ON TABLE FO_DB.SEREN.FO_ARTIFACTS TO ROLE FO_WRITER;

-- 4. External-browser SSO users need no further secret. If you prefer
--    password or key-pair auth, provision accordingly and set the
--    SNOWFLAKE_PASSWORD or SNOWFLAKE_PRIVATE_KEY_PATH env var at invocation
--    time — NEVER in config.json.
