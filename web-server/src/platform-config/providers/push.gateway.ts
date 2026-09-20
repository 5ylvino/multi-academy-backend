import { Injectable, Logger } from '@nestjs/common';

export interface PushGateway {
  readonly id: string;
  send(input: {
    tokens: string[];
    title: string;
    body: string;
    data?: Record<string, string>;
  }): Promise<{ messageId: string }>;
}

export class DisabledPushGateway implements PushGateway {
  readonly id = 'disabled';
  async send(): Promise<{ messageId: string }> {
    throw new Error('Push gateway disabled');
  }
}

/** Phase 4 stub — FCM/OneSignal wire later; control selects providerId. */
@Injectable()
export class StubPushGateway implements PushGateway {
  readonly id = 'fcm';
  private readonly logger = new Logger(StubPushGateway.name);

  async send(input: {
    tokens: string[];
    title: string;
    body: string;
    data?: Record<string, string>;
  }): Promise<{ messageId: string }> {
    if (process.env.NODE_ENV === 'production') {
      throw new Error('Push gateway is not configured');
    }
    const messageId = `push_stub_${Date.now()}`;
    this.logger.log(
      `Stub push to ${input.tokens.length} token(s): ${input.title} — ${input.body.slice(0, 80)}`,
    );
    return { messageId };
  }
}
