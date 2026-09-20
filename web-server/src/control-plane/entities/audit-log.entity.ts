import { Column, Entity, Index, PrimaryColumn } from 'typeorm';

@Entity({ name: 'audit_logs' })
export class AuditLogEntity {
  @PrimaryColumn('uuid')
  id!: string;

  @Index()
  @Column({ name: 'userid', type: 'varchar', length: 64, nullable: true })
  userId!: string | null;

  @Index()
  @Column({ name: 'tenantid', type: 'varchar', length: 64, nullable: true })
  tenantId!: string | null;

  @Column({ type: 'varchar', length: 16 })
  method!: string;

  @Index()
  @Column({ type: 'varchar', length: 255 })
  path!: string;

  @Column({ name: 'statuscode', type: 'int' })
  statusCode!: number;

  @Column({ type: 'varchar', length: 255, nullable: true })
  ip!: string | null;

  @Column({ name: 'useragent', type: 'varchar', length: 512, nullable: true })
  userAgent!: string | null;

  @Column({ name: 'payloadjson', type: 'text', nullable: true })
  payloadJson!: string | null;

  @Column({ name: 'responsejson', type: 'text', nullable: true })
  responseJson!: string | null;

  @Column({ name: 'createdat', type: 'timestamp', default: () => 'CURRENT_TIMESTAMP' })
  createdAt!: Date;
}

