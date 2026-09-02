-- Runs once when the postgres-app volume is first initialized.
-- app_test is the disposable target for integration tests (TEST_DATABASE_URL);
-- the application database itself is created by the image from POSTGRES_DB.
CREATE DATABASE app_test;
