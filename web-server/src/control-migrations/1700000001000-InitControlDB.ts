import { MigrationInterface, QueryRunner } from 'typeorm';

export class InitControlDB1700000001000 implements MigrationInterface {
  name = 'InitControlDB1700000001000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS tenants_configs (
        id varchar(64) PRIMARY KEY,
        slug varchar(255) NOT NULL UNIQUE,
        schoolBusinessOrganisationId varchar(64) NOT NULL UNIQUE,
        name varchar(255) NOT NULL,
        status varchar(32) NOT NULL,
        dbName varchar(128) NOT NULL,
        dbUri varchar(512) NOT NULL,
        schoolLevels TEXT NULL,
        ownerRegistrationPayloadJson TEXT NULL,
        ownerStagedPasswordHash varchar(255) NULL,
        ownerEmailVerified boolean NOT NULL DEFAULT false,
        onboardingToken varchar(255) NULL,
        onboardingTokenExpiresAt TIMESTAMP NULL,
        provisioningStatus varchar(32) NOT NULL,
        provisioningError TEXT NULL,
        createdAt TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updatedAt TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);

    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS user_tenant_mappings (
        id varchar(64) PRIMARY KEY,
        userId varchar(64) NOT NULL,
        tenantId varchar(64) NOT NULL,
        email varchar(255) NOT NULL,
        isPrimaryTenant boolean NOT NULL DEFAULT false,
        isActive boolean NOT NULL DEFAULT true,
        createdAt TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updatedAt TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
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
      );
    `);
    await queryRunner.query(
      `CREATE INDEX IF NOT EXISTS idx_registration_staging_email ON registration_staging (email);`,
    );

    await queryRunner.query(`CREATE INDEX IF NOT EXISTS idx_user_email ON user_tenant_mappings (email);`);
    await queryRunner.query(`CREATE INDEX IF NOT EXISTS idx_user ON user_tenant_mappings (userId);`);
    await queryRunner.query(`CREATE INDEX IF NOT EXISTS idx_tenant ON user_tenant_mappings (tenantId);`);

    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS audit_logs (
        id uuid PRIMARY KEY,
        userId varchar(64) NULL,
        tenantId varchar(64) NULL,
        method varchar(16) NOT NULL,
        path varchar(255) NOT NULL,
        statusCode int NOT NULL,
        ip varchar(255) NULL,
        userAgent varchar(512) NULL,
        payloadJson TEXT NULL,
        responseJson TEXT NULL,
        createdAt TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        -- Indexes are created below (Postgres doesn't support INDEX inline in the same way)
        CONSTRAINT audit_logs_method_check CHECK (char_length(method) <= 16)
      );
    `);

    await queryRunner.query(`CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs (userId);`);
    await queryRunner.query(`CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_logs (tenantId);`);
    await queryRunner.query(`CREATE INDEX IF NOT EXISTS idx_audit_path ON audit_logs (path);`);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`DROP TABLE IF EXISTS user_tenant_mappings;`);
    await queryRunner.query(`DROP TABLE IF EXISTS registration_staging;`);
    await queryRunner.query(`DROP TABLE IF EXISTS tenants_configs;`);
  }
}

