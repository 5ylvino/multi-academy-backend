import {
  ConnectedSocket,
  MessageBody,
  OnGatewayConnection,
  OnGatewayDisconnect,
  SubscribeMessage,
  WebSocketGateway,
  WebSocketServer,
} from '@nestjs/websockets';
import { Logger } from '@nestjs/common';
import { Server, Socket } from 'socket.io';
import { IdentificationService } from '../identification/identification.service';
import { FeatureFlagService } from '../platform-config/feature-flag.service';

@WebSocketGateway({
  cors: {
    origin: (process.env.CORS_ORIGINS || process.env.CORS_ORGINS || 'http://localhost:3000')
      .split(',')
      .map((s) => s.trim())
      .map((s) => s.replace(/\/$/, ''))
      .filter(Boolean),
    credentials: true,
  },
  transports: ['websocket', 'polling'],
})
export class RealtimeGateway implements OnGatewayConnection, OnGatewayDisconnect {
  private readonly logger = new Logger(RealtimeGateway.name);

  @WebSocketServer()
  server: Server;

  constructor(
    private readonly identification: IdentificationService,
    private readonly flags: FeatureFlagService,
  ) {}

  async handleConnection(client: Socket) {
    try {
      const token =
        (client.handshake.auth?.token as string) ||
        (client.handshake.headers?.authorization as string)?.replace(/^Bearer\s+/i, '');
      if (!token) {
        client.disconnect(true);
        return;
      }
      const claims = this.identification.verifyAccessToken(token);
      const tenantId = claims.tenant_id;
      const enabled = await this.flags.resolve(tenantId, 'comms.realtime_ws');
      if (!enabled) {
        client.emit('error', { message: 'Feature disabled: comms.realtime_ws' });
        client.disconnect(true);
        return;
      }
      (client.data as any).user = claims;
      await client.join(`tenant:${tenantId}`);
      await client.join(`user:${claims.user_id || claims.sub}`);
      client.emit('connected', { tenantId, userId: claims.user_id || claims.sub });
      this.logger.debug(`WS connected user=${claims.user_id} tenant=${tenantId}`);
    } catch (err) {
      this.logger.warn(`WS auth failed: ${(err as Error).message}`);
      client.disconnect(true);
    }
  }

  handleDisconnect(client: Socket) {
    this.logger.debug(`WS disconnected ${client.id}`);
  }

  @SubscribeMessage('ping')
  handlePing(@ConnectedSocket() client: Socket, @MessageBody() body: any) {
    client.emit('pong', { t: Date.now(), echo: body });
  }

  emitToTenant(tenantId: string, event: string, payload: unknown) {
    this.server?.to(`tenant:${tenantId}`).emit(event, payload);
  }

  emitToUser(userId: string, event: string, payload: unknown) {
    this.server?.to(`user:${userId}`).emit(event, payload);
  }
}
