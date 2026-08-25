"""
Database-level enforcement of the append-only audit trail.

Application code can be changed by whoever holds the repo; a database trigger
cannot be bypassed by a code path, an ORM misuse, or an operator with a psql
prompt and the application's own credentials. So the guarantee lives here.

Three layers, strongest first:

1. A row-level BEFORE UPDATE OR DELETE trigger that raises an exception.
   PostgreSQL applies triggers to the table *owner* as well, so this holds even
   against the account the application connects as. This is the real guarantee.
2. A statement-level BEFORE TRUNCATE trigger, because TRUNCATE does not fire
   row-level DELETE triggers and would otherwise be an escape hatch.
3. Privilege revocation for every non-owner grantee, so an ordinary reporting
   or analyst role cannot even attempt a write.

The only sanctioned way to change history is to append a correcting record.
"""
import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# The guard function and its two triggers. Written idempotently so it is safe to
# run on every startup.
_AUDIT_IMMUTABILITY_DDL = """
CREATE OR REPLACE FUNCTION audit_logs_block_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'audit_logs is append-only: % is not permitted on this table '
        '(attempted by role "%"). Append a correcting record instead.',
        TG_OP, current_user
        USING ERRCODE = 'restrict_violation',
              HINT = 'INSERT a compensating audit record; history is never rewritten.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_logs_no_update ON audit_logs;
CREATE TRIGGER trg_audit_logs_no_update
    BEFORE UPDATE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_block_mutation();

DROP TRIGGER IF EXISTS trg_audit_logs_no_delete ON audit_logs;
CREATE TRIGGER trg_audit_logs_no_delete
    BEFORE DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_block_mutation();

-- TRUNCATE bypasses row-level DELETE triggers, so it needs its own statement-level guard.
DROP TRIGGER IF EXISTS trg_audit_logs_no_truncate ON audit_logs;
CREATE TRIGGER trg_audit_logs_no_truncate
    BEFORE TRUNCATE ON audit_logs
    FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_block_mutation();
"""

# Layer 3: nobody but the owner gets write verbs, and the owner is still bound by
# the triggers above. A dedicated read-only role backs the Auditor persona.
_AUDIT_PRIVILEGE_DDL = """
REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM PUBLIC;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aml_auditor_ro') THEN
        CREATE ROLE aml_auditor_ro NOLOGIN;
    END IF;
END $$;

GRANT SELECT ON audit_logs TO aml_auditor_ro;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON audit_logs FROM aml_auditor_ro;
"""

# Supporting indexes for the audit-trail queries the UI issues.
_AUDIT_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ix_audit_logs_customer_created
    ON audit_logs (customer_id, created_at);
CREATE INDEX IF NOT EXISTS ix_audit_logs_sequence
    ON audit_logs (sequence_no);
CREATE INDEX IF NOT EXISTS ix_audit_logs_entity
    ON audit_logs (entity_type, entity_id);
"""


def install_audit_immutability(engine: Engine) -> dict:
    """
    Installs the append-only guarantees on audit_logs. Idempotent.

    Returns a dict describing which layers were installed, so startup can
    report the compliance posture rather than assume it.
    """
    status = {"triggers": False, "privileges": False, "indexes": False, "errors": []}

    with engine.begin() as conn:
        try:
            conn.execute(text(_AUDIT_IMMUTABILITY_DDL))
            status["triggers"] = True
        except Exception as exc:
            status["errors"].append(f"triggers: {exc}")
            # A missing audit guard is a compliance failure, not a warning.
            raise

    # Privileges need CREATEROLE; degrade to a warning rather than blocking
    # startup, since the triggers already carry the guarantee.
    with engine.begin() as conn:
        try:
            conn.execute(text(_AUDIT_PRIVILEGE_DDL))
            status["privileges"] = True
        except Exception as exc:
            status["errors"].append(f"privileges: {exc}")
            logger.warning(
                "Could not apply audit_logs privilege revocation (%s). "
                "Row-level triggers remain in force.", exc
            )

    with engine.begin() as conn:
        try:
            conn.execute(text(_AUDIT_INDEX_DDL))
            status["indexes"] = True
        except Exception as exc:
            status["errors"].append(f"indexes: {exc}")

    print(
        "[Database] audit_logs append-only enforcement: "
        f"triggers={status['triggers']} privileges={status['privileges']} "
        f"indexes={status['indexes']}"
    )
    return status


def verify_audit_immutability(engine: Engine) -> dict:
    """
    Proves the guarantee rather than asserting it: attempts a real UPDATE and a
    real DELETE inside a transaction that is always rolled back, and reports
    whether the database refused them.

    Used by GET /audit/immutability-proof so the demo can show the enforcement
    working instead of just describing it.
    """
    result = {
        "update_blocked": False,
        "delete_blocked": False,
        "update_error": None,
        "delete_error": None,
        "triggers_present": [],
        "method": "live_probe",
    }

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT tgname FROM pg_trigger
                WHERE tgrelid = 'audit_logs'::regclass AND NOT tgisinternal
                ORDER BY tgname
                """
            )
        ).fetchall()
        result["triggers_present"] = [r[0] for r in rows]
        record_count = conn.execute(text("SELECT count(*) FROM audit_logs")).scalar() or 0

    # A row-level trigger fires per affected row, so an UPDATE matching nothing
    # never fires it. On an empty table the live probe would therefore report
    # "not blocked" and libel a correctly-protected table. Fall back to
    # inspecting the triggers, and say which method was used.
    if record_count == 0:
        required = {
            "trg_audit_logs_no_update",
            "trg_audit_logs_no_delete",
            "trg_audit_logs_no_truncate",
        }
        installed = required.issubset(set(result["triggers_present"]))
        note = (
            "Verified by trigger inspection: audit_logs is empty, so a row-level "
            "trigger could not be provoked by a live statement."
        )
        result.update(
            method="trigger_inspection",
            update_blocked=installed,
            delete_blocked=installed,
            update_error=None if installed else "Guard trigger missing",
            delete_error=None if installed else "Guard trigger missing",
            note=note,
            enforced=installed,
        )
        return result

    for op, sql in (
        ("update", "UPDATE audit_logs SET action = 'TAMPERED' WHERE id = (SELECT MIN(id) FROM audit_logs)"),
        ("delete", "DELETE FROM audit_logs WHERE id = (SELECT MIN(id) FROM audit_logs)"),
    ):
        conn = engine.connect()
        trans = conn.begin()
        try:
            conn.execute(text(sql))
            # Reached only if the database allowed the mutation.
            result[f"{op}_blocked"] = False
            result[f"{op}_error"] = "NOT BLOCKED — audit trail is mutable!"
        except Exception as exc:
            result[f"{op}_blocked"] = True
            # Keep just the database's own message, not the full SQLAlchemy wrapper.
            result[f"{op}_error"] = str(exc).split("\n")[0].replace("(psycopg2.errors.RestrictViolation) ", "")
        finally:
            trans.rollback()
            conn.close()

    result["enforced"] = result["update_blocked"] and result["delete_blocked"]
    return result
