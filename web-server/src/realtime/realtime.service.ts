import { Injectable } from '@nestjs/common';
import { RealtimeGateway } from './realtime.gateway';

@Injectable()
export class RealtimeService {
  constructor(private readonly gateway: RealtimeGateway) {}

  notifyTenant(tenantId: string, event: string, payload: unknown) {
    this.gateway.emitToTenant(tenantId, event, payload);
  }

  notifyUser(userId: string, event: string, payload: unknown) {
    this.gateway.emitToUser(userId, event, payload);
  }

  notification(
    tenantId: string,
    userId: string,
    data: { title: string; message: string; type?: string },
  ) {
    this.gateway.emitToUser(userId, 'notification', data);
    this.gateway.emitToTenant(tenantId, 'notification', { ...data, userId });
  }
}
