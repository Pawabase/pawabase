-- Moves resource data from the old layout to the new one, on the shared Postgres database.
--
--   old:  public.pb_<scope>_<table>        (one table per resource, renamed with a prefix)
--   new:  <schema pb_<scope>>.<table>      (one schema per environment, tables keep their names)
--
-- Run it AFTER the new version is up and the new tables exist (Studio → your environment → Database → Create tables):
--
--   docker compose exec -T postgres psql -U pawabase -d pawabase < scripts/move-prefixed-tables.sql
--
-- It copies rows column by column (only columns both tables have), keeps ids, moves each id sequence past the highest id, and never overwrites a row that is already there.
-- It does not delete the old tables: check the data, then drop them (the last lines print the statements).
DO $$
DECLARE
  old record; scope text; tbl text; cols text; moved bigint; total bigint := 0; tables int := 0;
BEGIN
  FOR old IN
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name ~ '^pb_[0-9a-f]{12}_.+'
    ORDER BY table_name
  LOOP
    scope := substr(old.table_name, 1, 15);
    tbl := substr(old.table_name, 17);
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = scope AND table_name = tbl) THEN
      RAISE NOTICE 'skipped %: no table %.% yet (run "Create tables" first)', old.table_name, scope, tbl;
      CONTINUE;
    END IF;
    SELECT string_agg(format('%I', a.column_name), ', ') INTO cols
      FROM information_schema.columns a
      JOIN information_schema.columns b ON b.column_name = a.column_name AND b.table_schema = scope AND b.table_name = tbl
      WHERE a.table_schema = 'public' AND a.table_name = old.table_name;
    EXECUTE format('INSERT INTO %I.%I (%s) SELECT %s FROM public.%I ON CONFLICT DO NOTHING', scope, tbl, cols, cols, old.table_name);
    GET DIAGNOSTICS moved = ROW_COUNT;
    total := total + moved; tables := tables + 1;
    IF moved > 0 AND pg_get_serial_sequence(format('%I.%I', scope, tbl), 'id') IS NOT NULL THEN
      EXECUTE format('SELECT setval(pg_get_serial_sequence(%L, ''id''), (SELECT max(id) FROM %I.%I))', format('%I.%I', scope, tbl), scope, tbl);
    END IF;
    IF moved > 0 THEN RAISE NOTICE '% -> %.%: % rows', old.table_name, scope, tbl, moved; END IF;
  END LOOP;
  RAISE NOTICE 'done: % tables, % rows copied', tables, total;
END $$;

-- When the data checks out, free the old tables:
SELECT format('DROP TABLE public.%I;', table_name) AS "drop when satisfied"
FROM information_schema.tables WHERE table_schema = 'public' AND table_name ~ '^pb_[0-9a-f]{12}_.+' ORDER BY table_name LIMIT 3;
