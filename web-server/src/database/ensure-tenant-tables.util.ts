/**
 * Thin safety net for tenant DDL — mirrors migrations/1700000002000-Phase2Phase3Tables.
 * Provisioning should run TypeORM migrations; this helper reduces drift for legacy tenants.
 */
export async function ensureFromMigration(ds: { query: (sql: string) => Promise<unknown> }, statements: string[]) {
  for (const sql of statements) {
    await ds.query(sql);
  }
}

/** Phase 2/3 gateway payment tables — used by GatewayPaymentsService.ensureGatewayTables */
export const GATEWAY_PAYMENT_TABLES = [
  `CREATE TABLE IF NOT EXISTS financial_checkout_sessions (
    id varchar(64) PRIMARY KEY,
    student_id varchar(64) NOT NULL,
    invoice_id varchar(64) NULL,
    installment_id varchar(64) NULL,
    amount decimal(15,2) NOT NULL DEFAULT 0,
    currency varchar(8) NOT NULL DEFAULT 'NGN',
    reference varchar(128) NOT NULL UNIQUE,
    provider_id varchar(64) NOT NULL,
    provider_reference varchar(128) NULL,
    status varchar(32) NOT NULL DEFAULT 'pending',
    kind varchar(32) NOT NULL DEFAULT 'invoice',
    metadata text NULL,
    created_by varchar(64) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    settled_at TIMESTAMP NULL
  )`,
  `CREATE TABLE IF NOT EXISTS financial_installment_plans (
    id varchar(64) PRIMARY KEY,
    student_id varchar(64) NOT NULL,
    invoice_id varchar(64) NULL,
    title varchar(255) NOT NULL,
    total_amount decimal(15,2) NOT NULL DEFAULT 0,
    currency varchar(8) NOT NULL DEFAULT 'NGN',
    status varchar(32) NOT NULL DEFAULT 'active',
    created_by varchar(64) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS financial_installments (
    id varchar(64) PRIMARY KEY,
    plan_id varchar(64) NOT NULL,
    sequence int NOT NULL DEFAULT 1,
    amount decimal(15,2) NOT NULL DEFAULT 0,
    due_date varchar(32) NULL,
    status varchar(32) NOT NULL DEFAULT 'pending',
    paid_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS financial_advance_credits (
    id varchar(64) PRIMARY KEY,
    student_id varchar(64) NOT NULL,
    amount decimal(15,2) NOT NULL DEFAULT 0,
    currency varchar(8) NOT NULL DEFAULT 'NGN',
    term varchar(32) NULL,
    session_label varchar(64) NULL,
    payment_id varchar(64) NULL,
    reference varchar(128) NULL,
    notes text NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `ALTER TABLE financial_payments ADD COLUMN IF NOT EXISTS provider_id varchar(64) NULL`,
  `ALTER TABLE financial_payments ADD COLUMN IF NOT EXISTS provider_reference varchar(128) NULL`,
  `ALTER TABLE financial_payments ADD COLUMN IF NOT EXISTS invoice_id varchar(64) NULL`,
  `ALTER TABLE financial_payments ADD COLUMN IF NOT EXISTS installment_id varchar(64) NULL`,
  `ALTER TABLE financial_payments ADD COLUMN IF NOT EXISTS receipt_number varchar(64) NULL`,
  `ALTER TABLE financial_installment_plans ADD COLUMN IF NOT EXISTS surcharge_percent decimal(7,2) NOT NULL DEFAULT 0`,
  `ALTER TABLE financial_installment_plans ADD COLUMN IF NOT EXISTS created_by varchar(64) NULL`,
  `ALTER TABLE financial_invoices ADD COLUMN IF NOT EXISTS payment_plan_type varchar(32) NOT NULL DEFAULT 'full'`,
  `CREATE TABLE IF NOT EXISTS financial_fee_reminders (
    id varchar(64) PRIMARY KEY,
    invoice_id varchar(64) NOT NULL,
    student_id varchar(64) NOT NULL,
    due_date varchar(32) NULL,
    status varchar(32) NOT NULL DEFAULT 'pending',
    next_send_at TIMESTAMP NULL,
    last_sent_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
];
