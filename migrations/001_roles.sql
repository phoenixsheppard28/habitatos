-- Group roles. Grant membership to login roles: the pipeline gets habitat_writer, consumers get habitat_reader.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'habitat_reader') THEN
        CREATE ROLE habitat_reader NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'habitat_writer') THEN
        CREATE ROLE habitat_writer NOLOGIN;
    END IF;
END
$$;

GRANT habitat_reader TO habitat_writer;
GRANT USAGE ON SCHEMA public TO habitat_reader;
GRANT USAGE ON SCHEMA extensions TO habitat_reader;
