import { MigrationInterface, QueryRunner } from 'typeorm';

export class GeneratedResults1700000020000 implements MigrationInterface {
  name = 'GeneratedResults1700000020000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS academic_generated_results (
        id varchar(64) PRIMARY KEY,
        session_id varchar(64) NOT NULL,
        term_id varchar(64) NOT NULL,
        class_id varchar(64) NOT NULL,
        student_id varchar(64) NOT NULL,
        student_school_level varchar(32) NULL,
        student_academic_session varchar(128) NULL,
        student_term varchar(128) NULL,
        student_gender varchar(16) NULL,
        student_name varchar(255) NOT NULL,
        student_class_name varchar(255) NULL,
        student_class_code varchar(64) NULL,
        student_class_teacher_id varchar(64) NULL,
        student_class_teacher_name varchar(255) NULL,
        no_of_times_school_opened int NOT NULL DEFAULT 0,
        admission_no varchar(128) NULL,
        no_of_times_present int NOT NULL DEFAULT 0,
        term_ended varchar(64) NULL,
        no_of_times_absent int NOT NULL DEFAULT 0,
        next_term_begins varchar(64) NULL,
        student_subject_records text NOT NULL DEFAULT '[]',
        release_status varchar(32) NOT NULL DEFAULT 'pending',
        approved_by varchar(255) NULL,
        approved_at TIMESTAMP NULL,
        generated_by varchar(255) NOT NULL,
        generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (session_id, term_id, class_id, student_id)
      )
    `);
    await queryRunner.query(
      `CREATE INDEX IF NOT EXISTS idx_generated_results_scope
       ON academic_generated_results (session_id, term_id, class_id)`,
    );
    await queryRunner.query(
      `CREATE INDEX IF NOT EXISTS idx_generated_results_release
       ON academic_generated_results (release_status)`,
    );
    // Preserve legacy subject rows as one student-level record where possible.
    // The legacy table has no session_id, so the term's session is used.
    await queryRunner.query(`
      INSERT INTO academic_generated_results
        (id, session_id, term_id, class_id, student_id, student_academic_session,
         student_term, student_name, student_class_name, student_class_code,
         admission_no, student_subject_records, release_status, generated_by,
         generated_at, created_at, updated_at)
      SELECT
        'migrated-' || md5(ar.student_id || ':' || ar.class_id || ':' || ar.term_id),
        t.session_id, ar.term_id, ar.class_id, ar.student_id, ses.name, t.name,
        u.name, c.name, c.code, u.admission_no,
        json_agg(json_build_object(
          'subjectId', ar.subject_id, 'subject', sub.name,
          'caScore', COALESCE(ar.ca_score, 0), 'examScore', COALESCE(ar.exam_score, 0),
          'totalScore', COALESCE(ar.total_score, 0), 'totalAverage', COALESCE(ar.total_score, 0),
          'grade_title', COALESCE(ar.grade, 'F'), 'grade_name', COALESCE(ar.grade, 'Failed')
        )),
        CASE WHEN bool_and(ar.status = 'approved') THEN 'approved' ELSE 'pending' END,
        'migration', MAX(ar.created_at), MIN(ar.created_at), MAX(ar.updated_at)
      FROM academic_results ar
      JOIN academic_terms t ON t.id = ar.term_id
      JOIN academic_sessions ses ON ses.id = t.session_id
      LEFT JOIN users u ON u.id = ar.student_id
      LEFT JOIN academic_classes c ON c.id = ar.class_id
      LEFT JOIN academic_subjects sub ON sub.id = ar.subject_id
      WHERE ar.student_id IS NOT NULL
      GROUP BY ar.student_id, ar.class_id, ar.term_id, t.session_id, ses.name,
        t.name, u.name, c.name, c.code, u.admission_no
      ON CONFLICT (session_id, term_id, class_id, student_id) DO NOTHING
    `);
  }

  public async down(): Promise<void> {
    // Generated results are retained during rollback to avoid data loss.
  }
}
