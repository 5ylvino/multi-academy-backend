import { MigrationInterface, QueryRunner } from 'typeorm';

export class EisWebFoundation1700000014000 implements MigrationInterface {
  name = 'EisWebFoundation1700000014000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    const statements = [
      `CREATE TABLE IF NOT EXISTS parent_activation_codes (
        id varchar(64) PRIMARY KEY,
        parent_id varchar(64) NOT NULL,
        code_hash varchar(128) NOT NULL,
        code_hint varchar(16) NOT NULL,
        student_ids text NOT NULL DEFAULT '[]',
        expires_at TIMESTAMP NOT NULL,
        used_at TIMESTAMP NULL,
        recovered_at TIMESTAMP NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE INDEX IF NOT EXISTS idx_parent_activation_parent
        ON parent_activation_codes (parent_id, used_at)`,
      `CREATE TABLE IF NOT EXISTS authorized_pickup_adults (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        name varchar(255) NOT NULL,
        phone varchar(64) NULL,
        photo_url text NULL,
        relationship varchar(64) NULL,
        id_number varchar(128) NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS pickup_delegations (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        adult_name varchar(255) NOT NULL,
        phone varchar(64) NULL,
        photo_url text NULL,
        id_capture text NULL,
        valid_on varchar(32) NOT NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS safeguarding_exeats (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        kind varchar(32) NOT NULL,
        reason text NOT NULL,
        pickup_at TIMESTAMP NOT NULL,
        pickup_adult_name varchar(255) NOT NULL,
        pickup_adult_phone varchar(64) NULL,
        pickup_adult_id varchar(64) NULL,
        status varchar(32) NOT NULL,
        form_teacher_id varchar(64) NULL,
        form_teacher_at TIMESTAMP NULL,
        principal_id varchar(64) NULL,
        principal_at TIMESTAMP NULL,
        parent_otp_hash varchar(128) NULL,
        parent_otp_expires TIMESTAMP NULL,
        parent_confirmed_at TIMESTAMP NULL,
        pass_code varchar(16) NULL,
        pass_expires_at TIMESTAMP NULL,
        gate_used_at TIMESTAMP NULL,
        gate_used_by varchar(64) NULL,
        decision_note text NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE INDEX IF NOT EXISTS idx_safeguarding_exeats_student
        ON safeguarding_exeats (student_id, status)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS idx_safeguarding_exeats_pass
        ON safeguarding_exeats (pass_code) WHERE pass_code IS NOT NULL`,
      `CREATE TABLE IF NOT EXISTS announcement_acks (
        announcement_id varchar(64) NOT NULL,
        user_id varchar(64) NOT NULL,
        acknowledged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (announcement_id, user_id)
      )`,
      `ALTER TABLE announcements ADD COLUMN IF NOT EXISTS is_critical boolean NOT NULL DEFAULT false`,
      `ALTER TABLE financial_payments ADD COLUMN IF NOT EXISTS invoice_id varchar(64) NULL`,
      `ALTER TABLE financial_payments ADD COLUMN IF NOT EXISTS receipt_number varchar(64) NULL`,
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
      `ALTER TABLE financial_installment_plans ADD COLUMN IF NOT EXISTS surcharge_percent decimal(7,2) NOT NULL DEFAULT 0`,
    ];
    for (const sql of statements) {
      await queryRunner.query(sql);
    }
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`DROP TABLE IF EXISTS financial_fee_reminders`);
    await queryRunner.query(`DROP TABLE IF EXISTS announcement_acks`);
    await queryRunner.query(`DROP TABLE IF EXISTS safeguarding_exeats`);
    await queryRunner.query(`DROP TABLE IF EXISTS pickup_delegations`);
    await queryRunner.query(`DROP TABLE IF EXISTS authorized_pickup_adults`);
    await queryRunner.query(`DROP TABLE IF EXISTS parent_activation_codes`);
  }
}
