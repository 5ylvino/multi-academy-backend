/** Port interfaces — domain code talks to these; adapters implement vendors. */

export interface CreateCheckoutInput {
  amountMinor: number;
  currency: string;
  reference: string;
  email: string;
  callbackUrl: string;
  metadata?: Record<string, unknown>;
}

export interface CheckoutSession {
  providerId: string;
  reference: string;
  checkoutUrl: string;
  accessCode?: string;
}

export interface PaymentVerification {
  providerId: string;
  reference: string;
  status: 'success' | 'failed' | 'pending';
  amountMinor: number;
  currency: string;
  providerReference?: string;
  raw?: unknown;
}

export interface VerifyTransactionInput {
  reference: string;
  tenantId: string;
}

export interface WebhookResult {
  reference: string;
  status: 'success' | 'failed' | 'ignored';
  amountMinor?: number;
  currency?: string;
}

export interface PaymentGateway {
  readonly id: string;
  createCheckout(input: CreateCheckoutInput): Promise<CheckoutSession>;
  /** Prefer VerifyTransactionInput; string form kept for older adapters. */
  verifyTransaction(
    referenceOrInput: string | VerifyTransactionInput,
    tenantId?: string,
  ): Promise<PaymentVerification>;
  handleWebhook(
    headers: Record<string, string>,
    rawBody: Buffer,
  ): Promise<WebhookResult>;
  refund?(input: { reference: string; amountMinor?: number }): Promise<{ status: string }>;
}

export interface SmsGateway {
  readonly id: string;
  send(input: {
    to: string;
    message: string;
    senderId?: string;
    tenantId?: string;
  }): Promise<{ messageId: string }>;
  sendBulk?(
    input: { to: string[]; message: string; senderId?: string; tenantId?: string },
  ): Promise<{ messageIds: string[] }>;
}

export interface EmailGateway {
  readonly id: string;
  send(input: {
    to: string | string[];
    subject: string;
    html?: string;
    text?: string;
    tenantId?: string;
  }): Promise<{ messageId: string }>;
}

export interface AiProvider {
  readonly id: string;
  chat(input: {
    messages: { role: string; content: string }[];
    model?: string;
    temperature?: number;
    maxTokens?: number;
  }): Promise<{ content: string; usage?: { promptTokens?: number; completionTokens?: number } }>;
}

export interface MeetingProvider {
  readonly id: string;
  createMeeting(input: {
    title: string;
    startsAt: string;
    endsAt?: string;
    attendees?: string[];
  }): Promise<{
    providerMeetingId: string;
    joinUrl: string;
    hostUrl?: string;
  }>;
  cancelMeeting?(providerMeetingId: string): Promise<void>;
}

/** Null adapters — used when capability is off or provider unresolved. */
export class DisabledPaymentGateway implements PaymentGateway {
  readonly id = 'disabled';
  async createCheckout(): Promise<CheckoutSession> {
    throw new Error('Payment gateway disabled');
  }
  async verifyTransaction(): Promise<PaymentVerification> {
    throw new Error('Payment gateway disabled');
  }
  async handleWebhook(): Promise<WebhookResult> {
    return { reference: '', status: 'ignored' };
  }
}

/** Normalize verifyTransaction args across adapters. */
export function resolveVerifyArgs(
  referenceOrInput: string | VerifyTransactionInput,
  tenantId?: string,
): { reference: string; tenantId: string } {
  if (typeof referenceOrInput === 'string') {
    return { reference: referenceOrInput, tenantId: tenantId || '' };
  }
  return {
    reference: referenceOrInput.reference,
    tenantId: referenceOrInput.tenantId || tenantId || '',
  };
}

export class DisabledSmsGateway implements SmsGateway {
  readonly id = 'disabled';
  async send(): Promise<{ messageId: string }> {
    throw new Error('SMS gateway disabled');
  }
}

export class DisabledEmailGateway implements EmailGateway {
  readonly id = 'disabled';
  async send(): Promise<{ messageId: string }> {
    throw new Error('Email gateway disabled');
  }
}

export class DisabledAiProvider implements AiProvider {
  readonly id = 'disabled';
  async chat(): Promise<{ content: string }> {
    throw new Error('AI provider disabled');
  }
}

export class DisabledMeetingProvider implements MeetingProvider {
  readonly id = 'disabled';
  async createMeeting(): Promise<{ providerMeetingId: string; joinUrl: string }> {
    throw new Error('Meeting provider disabled');
  }
}
