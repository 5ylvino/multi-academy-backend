import { MigrationInterface, QueryRunner } from 'typeorm';

export class EmailVerificationOnboarding1700000004000 implements MigrationInterface {
  name = 'EmailVerificationOnboarding1700000004000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      ALTER TABLE tenants_configs
      ADD COLUMN IF NOT EXISTS onboardingTokenExpiresAt TIMESTAMP NULL
    `);
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS registration_staging (
        id varchar(64) PRIMARY KEY,
        full_name varchar(255) NOT NULL,
        email varchar(255) NOT NULL,
        phone varchar(64) NULL,
        organization_name varchar(255) NOT NULL,
        school_levels TEXT NULL,
        encrypted_password TEXT NOT NULL,
        verification_token_hash varchar(64) NOT NULL UNIQUE,
        verification_expires_at TIMESTAMP NOT NULL,
        consumed_at TIMESTAMP NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await queryRunner.query(
      `CREATE INDEX IF NOT EXISTS idx_registration_staging_email ON registration_staging (email)`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      ALTER TABLE tenants_configs
      DROP COLUMN IF EXISTS onboardingTokenExpiresAt
    `);
    await queryRunner.query(`DROP TABLE IF EXISTS registration_staging`);
  }
}
