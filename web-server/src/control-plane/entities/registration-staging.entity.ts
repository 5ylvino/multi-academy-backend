import { Column, Entity, Index, PrimaryColumn } from 'typeorm';

/**
 * Temporary, pre-verification onboarding data.
 *
 * The password is encrypted at rest only long enough to complete onboarding;
 * it is decrypted in memory and replaced with the normal password hash when
 * the tenant user is created. Raw passwords and verification tokens are never
 * persisted.
 */
@Entity({ name: 'registration_staging' })
export class RegistrationStagingEntity {
  @PrimaryColumn({ type: 'varchar', length: 64 })
  id!: string;

  @Column({ name: 'full_name', type: 'varchar', length: 255 })
  fullName!: string;

  @Index()
  @Column({ type: 'varchar', length: 255 })
  email!: string;

  @Column({ type: 'varchar', length: 64, nullable: true })
  phone!: string | null;

  @Column({ name: 'install_token_hash', type: 'varchar', length: 64, nullable: true })
  installTokenHash!: string | null;

  @Column({ name: 'device_hash', type: 'varchar', length: 128, nullable: true })
  deviceHash!: string | null;

  @Column({ name: 'organization_name', type: 'varchar', length: 255 })
  organizationName!: string;

  @Column({ name: 'school_levels', type: 'text', nullable: true })
  schoolLevels!: string | null;

  @Column({ name: 'encrypted_password', type: 'text' })
  encryptedPassword!: string;

  @Index({ unique: true })
  @Column({ name: 'verification_token_hash', type: 'varchar', length: 64 })
  verificationTokenHash!: string;

  @Column({ name: 'verification_expires_at', type: 'timestamp' })
  verificationExpiresAt!: Date;

  @Column({ name: 'consumed_at', type: 'timestamp', nullable: true })
  consumedAt!: Date | null;

  @Column({ name: 'created_at', type: 'timestamp', default: () => 'CURRENT_TIMESTAMP' })
  createdAt!: Date;
}
