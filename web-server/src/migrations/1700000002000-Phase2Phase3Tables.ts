import { MigrationInterface, QueryRunner } from 'typeorm';

/**
 * Phase 2/3 tenant tables — authoritative DDL mirrored from service ensureTables.
 * Service-level ensureTables remain thin CREATE IF NOT EXISTS safety nets (dual-path).
 */
export class Phase2Phase3Tables1700000002000 implements MigrationInterface {
  name = 'Phase2Phase3Tables1700000002000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    const statements = [
      // Gateway payments
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
      `ALTER TABLE financial_fee_structures ADD COLUMN IF NOT EXISTS currency varchar(8) NOT NULL DEFAULT 'NGN'`,
      `ALTER TABLE financial_invoices ADD COLUMN IF NOT EXISTS currency varchar(8) NOT NULL DEFAULT 'NGN'`,
      // Grading matrix
      `CREATE TABLE IF NOT EXISTS academic_grading_matrix (
        id varchar(64) PRIMARY KEY DEFAULT 'default',
        ca_weight int NOT NULL DEFAULT 40,
        exam_weight int NOT NULL DEFAULT 60,
        grade_bands text NOT NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      // Absence alerts
      `CREATE TABLE IF NOT EXISTS attendance_absence_alert_config (
        id varchar(64) PRIMARY KEY DEFAULT 'default',
        threshold_days int NOT NULL DEFAULT 3,
        window_days int NOT NULL DEFAULT 14,
        notify_parents boolean NOT NULL DEFAULT true,
        notify_staff boolean NOT NULL DEFAULT true,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS attendance_absence_alerts_sent (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        absence_count int NOT NULL,
        sent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      // Calendar
      `CREATE TABLE IF NOT EXISTS school_calendar_events (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        description text NULL,
        event_type varchar(64) NOT NULL DEFAULT 'general',
        starts_at TIMESTAMP NOT NULL,
        ends_at TIMESTAMP NULL,
        all_day boolean NOT NULL DEFAULT false,
        audience varchar(32) NOT NULL DEFAULT 'all',
        location varchar(255) NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      // Messaging
      `CREATE TABLE IF NOT EXISTS message_threads (
        id varchar(64) PRIMARY KEY,
        subject varchar(255) NOT NULL DEFAULT '',
        participant_ids text NOT NULL DEFAULT '[]',
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS messages (
        id varchar(64) PRIMARY KEY,
        thread_id varchar(64) NOT NULL,
        sender_id varchar(64) NOT NULL,
        body text NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages (thread_id, created_at)`,
      // Timetable
      `CREATE TABLE IF NOT EXISTS timetable_slots (
        id varchar(64) PRIMARY KEY,
        class_id varchar(64) NOT NULL,
        subject_id varchar(64) NULL,
        teacher_id varchar(64) NULL,
        day_of_week int NOT NULL,
        start_time varchar(8) NOT NULL,
        end_time varchar(8) NOT NULL,
        room varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      // CBT
      `CREATE TABLE IF NOT EXISTS cbt_exams (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        subject_id varchar(64) NULL,
        duration_minutes int NOT NULL DEFAULT 60,
        status varchar(32) NOT NULL DEFAULT 'draft',
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS cbt_questions (
        id varchar(64) PRIMARY KEY,
        exam_id varchar(64) NOT NULL,
        prompt text NOT NULL,
        options text NOT NULL DEFAULT '[]',
        correct_index int NOT NULL DEFAULT 0,
        points int NOT NULL DEFAULT 1
      )`,
      // Meetings
      `CREATE TABLE IF NOT EXISTS virtual_meetings (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        starts_at TIMESTAMP NOT NULL,
        ends_at TIMESTAMP NULL,
        provider_id varchar(64) NOT NULL,
        provider_meeting_id varchar(128) NOT NULL,
        join_url text NOT NULL,
        host_url text NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      // Library
      `CREATE TABLE IF NOT EXISTS library_items (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        author varchar(255) NULL,
        isbn varchar(64) NULL,
        copies int NOT NULL DEFAULT 1,
        available int NOT NULL DEFAULT 1,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS library_loans (
        id varchar(64) PRIMARY KEY,
        item_id varchar(64) NOT NULL,
        borrower_id varchar(64) NOT NULL,
        borrowed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        due_at TIMESTAMP NULL,
        returned_at TIMESTAMP NULL
      )`,
      // Consent
      `CREATE TABLE IF NOT EXISTS consent_slips (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        body text NOT NULL,
        slip_type varchar(64) NOT NULL DEFAULT 'permission',
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS consent_responses (
        id varchar(64) PRIMARY KEY,
        slip_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        guardian_name varchar(255) NULL,
        status varchar(32) NOT NULL DEFAULT 'pending',
        signed_at TIMESTAMP NULL,
        notes text NULL
      )`,
      // Emergency
      `CREATE TABLE IF NOT EXISTS emergency_broadcasts (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        message text NOT NULL,
        channels varchar(128) NOT NULL DEFAULT 'sms,email',
        audience varchar(64) NOT NULL DEFAULT 'all_parents',
        created_by varchar(64) NULL,
        sent_count int NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      // Fees ops
      `CREATE TABLE IF NOT EXISTS fee_split_settlements (
        id varchar(64) PRIMARY KEY,
        payment_reference varchar(128) NOT NULL,
        gross_minor int NOT NULL,
        school_share_minor int NOT NULL,
        saas_share_minor int NOT NULL,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        status varchar(32) NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS bank_recon_entries (
        id varchar(64) PRIMARY KEY,
        bank_reference varchar(128) NOT NULL,
        amount_minor int NOT NULL,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        matched_payment_ref varchar(128) NULL,
        status varchar(32) NOT NULL DEFAULT 'unmatched',
        notes text NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      // Nursery
      `CREATE TABLE IF NOT EXISTS nursery_developmental (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        domain varchar(64) NOT NULL,
        level varchar(32) NOT NULL,
        notes text NULL,
        observed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS nursery_wellness (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        mood varchar(32) NULL,
        appetite varchar(32) NULL,
        nap_minutes int NULL,
        notes text NULL,
        logged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS nursery_media_moments (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        url text NOT NULL,
        caption text NULL,
        logged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      // Push device tokens
      `CREATE TABLE IF NOT EXISTS push_device_tokens (
        id varchar(64) PRIMARY KEY,
        user_id varchar(64) NOT NULL,
        token text NOT NULL,
        platform varchar(32) NOT NULL DEFAULT 'web',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_used_at TIMESTAMP NULL,
        UNIQUE (user_id, token)
      )`,
      // Phase 4 finance extras
      `CREATE TABLE IF NOT EXISTS payroll_runs (
        id varchar(64) PRIMARY KEY,
        title varchar(255) NOT NULL,
        period_start varchar(32) NOT NULL,
        period_end varchar(32) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'draft',
        total_gross decimal(15,2) NOT NULL DEFAULT 0,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS payroll_paye_lines (
        id varchar(64) PRIMARY KEY,
        run_id varchar(64) NOT NULL,
        staff_id varchar(64) NOT NULL,
        gross decimal(15,2) NOT NULL DEFAULT 0,
        paye decimal(15,2) NOT NULL DEFAULT 0,
        net decimal(15,2) NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS inventory_items (
        id varchar(64) PRIMARY KEY,
        sku varchar(64) NULL,
        name varchar(255) NOT NULL,
        category varchar(64) NULL,
        qty_on_hand int NOT NULL DEFAULT 0,
        unit_cost decimal(15,2) NOT NULL DEFAULT 0,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS inventory_movements (
        id varchar(64) PRIMARY KEY,
        item_id varchar(64) NOT NULL,
        direction varchar(8) NOT NULL,
        qty int NOT NULL DEFAULT 0,
        reason varchar(255) NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS budget_lines (
        id varchar(64) PRIMARY KEY,
        category varchar(128) NOT NULL,
        term varchar(32) NOT NULL,
        session_label varchar(64) NULL,
        planned_amount decimal(15,2) NOT NULL DEFAULT 0,
        spent_amount decimal(15,2) NOT NULL DEFAULT 0,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      // Phase 4 ops
      `CREATE TABLE IF NOT EXISTS transport_routes (
        id varchar(64) PRIMARY KEY,
        name varchar(255) NOT NULL,
        stops text NULL,
        driver_id varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS transport_vehicles (
        id varchar(64) PRIMARY KEY,
        plate varchar(32) NOT NULL,
        capacity int NOT NULL DEFAULT 30,
        route_id varchar(64) NULL,
        status varchar(32) NOT NULL DEFAULT 'active',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS transport_assignments (
        id varchar(64) PRIMARY KEY,
        route_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        pickup_stop varchar(128) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS hostel_houses (
        id varchar(64) PRIMARY KEY,
        name varchar(255) NOT NULL,
        gender varchar(16) NULL,
        capacity int NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS hostel_beds (
        id varchar(64) PRIMARY KEY,
        house_id varchar(64) NOT NULL,
        label varchar(64) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'available',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS hostel_allocations (
        id varchar(64) PRIMARY KEY,
        bed_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        from_date varchar(32) NULL,
        to_date varchar(32) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS clinic_visits (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        symptoms text NULL,
        treatment text NULL,
        nurse_id varchar(64) NULL,
        visited_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS gate_pickup_tokens (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        guardian_name varchar(255) NULL,
        token_code varchar(32) NOT NULL UNIQUE,
        valid_until TIMESTAMP NOT NULL,
        used_at TIMESTAMP NULL,
        created_by varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      // Curriculum analytics stubs
      `CREATE TABLE IF NOT EXISTS waec_neco_results (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        exam_type varchar(16) NOT NULL,
        subject varchar(128) NOT NULL,
        score decimal(7,2) NOT NULL DEFAULT 0,
        session_label varchar(64) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS jamb_cbt_questions (
        id varchar(64) PRIMARY KEY,
        subject varchar(128) NOT NULL,
        prompt text NOT NULL,
        options text NOT NULL DEFAULT '[]',
        correct_index int NOT NULL DEFAULT 0,
        year int NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      // Phase 5 primary / secondary
      `CREATE TABLE IF NOT EXISTS primary_badges (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        badge_type varchar(64) NOT NULL,
        title varchar(255) NOT NULL,
        awarded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS primary_literacy_log (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        skill varchar(128) NOT NULL,
        level varchar(32) NOT NULL,
        notes text NULL,
        logged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE TABLE IF NOT EXISTS secondary_career_entries (
        id varchar(64) PRIMARY KEY,
        student_id varchar(64) NOT NULL,
        title varchar(255) NOT NULL,
        description text NULL,
        category varchar(64) NULL,
        url text NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`,
    ];

    for (const sql of statements) {
      await queryRunner.query(sql);
    }
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    void queryRunner;
  }
}
