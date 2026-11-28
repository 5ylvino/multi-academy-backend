-- Runtime role: data access only, never a superuser, never BYPASSRLS. Change the password outside dev.
CREATE ROLE masms_app LOGIN PASSWORD 'app' NOSUPERUSER NOBYPASSRLS;
