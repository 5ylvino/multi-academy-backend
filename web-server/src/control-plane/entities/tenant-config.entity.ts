import { Column, Entity, Index, PrimaryColumn } from 'typeorm';

@Entity({ name: 'tenants_configs' })
export class TenantConfigEntity {
  // tenant IDs in this codebase look like `tnt_<...>` so we store them as strings in Postgres.
  @PrimaryColumn({ type: 'varchar', length: 64 })
  id!: string;

  @Index({ unique: true })
  @Column({ type: 'varchar', length: 255 })
  slug!: string;

  @Index({ unique: true })
  @Column({ name: 'schoolbusinessorganisationid', type: 'varchar', length: 64 })
  schoolBusinessOrganisationId!: string;

  @Column({ type: 'varchar', length: 255 })
  name!: string;

  @Column({ type: 'varchar', length: 32 })
  status!: 'pending_verification' | 'active' | 'suspended' | 'deleted';

  @Column({ name: 'dbname', type: 'varchar', length: 128 })
  dbName!: string;

  @Column({ name: 'dburi', type: 'varchar', length: 512 })
  dbUri!: string;

  @Column({ name: 'schoollevels', type: 'text', nullable: true })
  schoolLevels!: string | null; // store as JSON string

  @Column({ name: 'ownerregistrationpayloadjson', type: 'text', nullable: true })
  ownerRegistrationPayloadJson!: string | null; // JSON string

  @Column({ name: 'ownerstagedpasswordhash', type: 'varchar', length: 255, nullable: true })
  ownerStagedPasswordHash!: string | null;

  @Column({ name: 'owneremailverified', type: 'boolean', default: false })
  ownerEmailVerified!: boolean;

  @Column({ name: 'onboardingtoken', type: 'varchar', length: 255, nullable: true })
  onboardingToken!: string | null;

  @Column({ name: 'onboardingtokenexpiresat', type: 'timestamp', nullable: true })
  onboardingTokenExpiresAt!: Date | null;

  @Column({ name: 'provisioningstatus', type: 'varchar', length: 32 })
  provisioningStatus!: 'pending' | 'ready' | 'failed';

  @Column({ name: 'provisioningerror', type: 'text', nullable: true })
  provisioningError!: string | null;

  @Column({ name: 'createdat', type: 'timestamp', default: () => 'CURRENT_TIMESTAMP' })
  createdAt!: Date;

  @Column({
    name: 'updatedat',
    type: 'timestamp',
    default: () => 'CURRENT_TIMESTAMP',
  })
  updatedAt!: Date;
}

