import { MigrationInterface, QueryRunner } from 'typeorm';

/**
 * Phase 1 formal tenant schema — authoritative DDL for school modules.
 * Provisioning runs TypeORM migrations; service-level ensureTables remain a
 * transitional safety net until all tenants are migrated.
 */
export class Phase1TenantSchema1700000001000 implements MigrationInterface {
  name = 'Phase1TenantSchema1700000001000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    const statements = [
      `CREATE TABLE IF NOT EXISTS users (
        id varchar(64) PRIMARY KEY,
        email varchar(255) NOT NULL UNIQUE,
        name varchar(255) NOT NULL,
        phone varchar(32) NULL,
        major varchar(128) NULL,
        password_hash varchar(255) NOT NULL,
        roles TEXT NOT NULL,
        permissions TEXT NOT NULL,
        capabilities TEXT NOT NULL,
        passport_photo_url text NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS academic_sessions (
        id varchar(64) PRIMARY KEY,
        name varchar(128) NOT NULL,
        start_date varchar(32) NULL,
        end_date varchar(32) NULL,
        is_current boolean NOT NULL DEFAULT false,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS academic_terms (
        id varchar(64) PRIMARY KEY,
        session_id varchar(64) NOT NULL,
        name varchar(64) NOT NULL,
        code varchar(32) NOT NULL,
        start_date varchar(32) NULL,
        end_date varchar(32) NULL,
        is_current boolean NOT NULL DEFAULT false,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS academic_classes (
        id varchar(64) PRIMARY KEY,
        name varchar(255) NOT NULL,
        code varchar(64) NOT NULL,
        school_level varchar(32) NOT NULL,
        class_teacher_id varchar(64) NULL,
        capacity int NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS academic_subjects (
        id varchar(64) PRIMARY KEY,
        name varchar(255) NOT NULL,
        code varchar(64) NOT NULL,
        school_level varchar(32) NOT NULL,
        description text NULL,
        class_ids TEXT NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS academic_assignments (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        description text NULL,
        class_id varchar(64) NULL,
        subject_id varchar(64) NULL,
        assigned_by varchar(64) NULL,
        due_date varchar(32) NOT NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS academic_results (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NULL,
        class_id varchar(64) NOT NULL,
        subject_id varchar(64) NOT NULL,
        term_id varchar(64) NOT NULL,
        ca_score decimal(10,2) NULL,
        exam_score decimal(10,2) NULL,
        total_score decimal(10,2) NULL,
        grade varchar(8) NULL,
        status varchar(32) NOT NULL DEFAULT 'draft',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS academic_class_teacher_links (
        id varchar(64) PRIMARY KEY,
        class_id varchar(64) NOT NULL,
        teacher_id varchar(64) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (class_id, teacher_id)
      )`,
      `CREATE TABLE IF NOT EXISTS academic_subject_teacher_links (
        id varchar(64) PRIMARY KEY,
        subject_id varchar(64) NOT NULL,
        teacher_id varchar(64) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (subject_id, teacher_id)
      )`,
      `CREATE TABLE IF NOT EXISTS parent_student_links (
        id varchar(64) PRIMARY KEY,
        parent_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (parent_id, student_id)
      )`,
      `CREATE TABLE IF NOT EXISTS student_class_enrollments (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        class_id varchar(64) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (student_id, class_id)
      )`,
      `CREATE TABLE IF NOT EXISTS attendance_students (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        class_id varchar(64) NULL,
        date varchar(32) NOT NULL,
        status varchar(32) NOT NULL,
        notes text NULL,
        marked_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS attendance_staff (
        id varchar(64) PRIMARY KEY,
        staff_id varchar(64) NOT NULL,
        date varchar(32) NOT NULL,
        status varchar(32) NOT NULL,
        check_in_at varchar(64) NULL,
        check_out_at varchar(64) NULL,
        method varchar(32) NULL,
        notes text NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS financial_fee_structures (
        id varchar(64) PRIMARY KEY,
        name varchar(255) NOT NULL,
        description text NULL,
        school_level varchar(32) NOT NULL,
        class_id varchar(64) NULL,
        amount decimal(15,2) NOT NULL DEFAULT 0,
        term varchar(32) NOT NULL,
        due_date varchar(32) NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS financial_payments (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        amount decimal(15,2) NOT NULL DEFAULT 0,
        method varchar(32) NOT NULL,
        reference varchar(255) NULL,
        date varchar(32) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'completed',
        biometric_verified boolean NOT NULL DEFAULT false,
        biometric_assertion_id varchar(128) NULL,
        recorded_by varchar(64) NULL,
        installment_id varchar(64) NULL,
        provider_id varchar(64) NULL,
        provider_reference varchar(255) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS financial_invoices (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        fee_structure_id varchar(64) NULL,
        title varchar(255) NOT NULL,
        amount decimal(15,2) NOT NULL DEFAULT 0,
        term varchar(32) NOT NULL,
        session_label varchar(64) NULL,
        status varchar(32) NOT NULL DEFAULT 'unpaid',
        payment_plan_type varchar(32) NOT NULL DEFAULT 'full',
        due_date varchar(32) NULL,
        notes text NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS financial_scholarships (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        title varchar(255) NOT NULL,
        amount decimal(15,2) NULL,
        percent decimal(7,2) NULL,
        term varchar(32) NOT NULL,
        session_label varchar(64) NULL,
        status varchar(32) NOT NULL DEFAULT 'active',
        notes text NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS financial_refunds (
        id varchar(64) PRIMARY KEY,
        payment_id varchar(64) NULL,
        student_id varchar(64) NOT NULL,
        amount decimal(15,2) NOT NULL DEFAULT 0,
        reason text NULL,
        method varchar(32) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'completed',
        processed_by varchar(64) NULL,
        date varchar(32) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS announcements (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        body text NOT NULL,
        audience varchar(64) NULL,
        created_by varchar(64) NULL,
        published_at varchar(64) NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS reports_generated (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        type varchar(64) NOT NULL,
        scope varchar(128) NOT NULL,
        period varchar(128) NOT NULL,
        generated_date varchar(32) NOT NULL,
        total_amount decimal(15,2) NULL,
        status varchar(32) NOT NULL DEFAULT 'draft',
        category varchar(32) NOT NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS biometric_credentials (
        id varchar(64) PRIMARY KEY,
        user_id varchar(64) NOT NULL,
        credential_id text NOT NULL,
        public_key text NOT NULL,
        counter bigint NOT NULL DEFAULT 0,
        transports text NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS business_org (
        id varchar(64) PRIMARY KEY,
        name varchar(255) NOT NULL,
        slug varchar(255) NULL,
        school_levels TEXT NULL,
        branding_json TEXT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS org_subscription (
        id varchar(64) PRIMARY KEY,
        plan_key varchar(64) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'active',
        started_at varchar(64) NULL,
        ends_at varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS org_invoices (
        id varchar(64) PRIMARY KEY,
        amount decimal(15,2) NOT NULL DEFAULT 0,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        status varchar(32) NOT NULL DEFAULT 'open',
        due_date varchar(32) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS public_org_branding (
        id varchar(64) PRIMARY KEY,
        slug varchar(255) NOT NULL UNIQUE,
        display_name varchar(255) NOT NULL,
        logo_url text NULL,
        primary_color varchar(32) NULL,
        welcome_message text NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
    ];

    for (const sql of statements) {
      await queryRunner.query(sql);
    }
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    // Non-destructive down: Phase 1 schema is additive baseline for tenants.
    void queryRunner;
  }
}
