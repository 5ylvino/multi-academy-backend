import { Column, Entity, Index, PrimaryColumn } from 'typeorm';

@Entity({ name: 'user_tenant_mappings' })
export class UserTenantMappingEntity {
  // user/tenant IDs are generated as string prefixes (`usr_*`, `tnt_*`).
  @PrimaryColumn({ type: 'varchar', length: 64 })
  id!: string;

  @Index()
  @Column({ name: 'userid', type: 'varchar', length: 64 })
  userId!: string;

  @Index()
  @Column({ name: 'tenantid', type: 'varchar', length: 64 })
  tenantId!: string;

  @Index()
  @Column({ type: 'varchar', length: 255 })
  email!: string;

  @Column({ name: 'isprimarytenant', type: 'boolean', default: false })
  isPrimaryTenant!: boolean;

  @Column({ name: 'isactive', type: 'boolean', default: true })
  isActive!: boolean;

  @Column({ name: 'createdat', type: 'timestamp', default: () => 'CURRENT_TIMESTAMP' })
  createdAt!: Date;

  @Column({
    name: 'updatedat',
    type: 'timestamp',
    default: () => 'CURRENT_TIMESTAMP',
  })
  updatedAt!: Date;
}

