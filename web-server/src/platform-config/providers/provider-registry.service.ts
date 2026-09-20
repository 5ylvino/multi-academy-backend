import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { RuntimeConfigService } from '../runtime-config.service';
import { ProviderSecretsService } from '../provider-secrets.service';
import {
  AiProvider,
  DisabledAiProvider,
  DisabledEmailGateway,
  DisabledMeetingProvider,
  DisabledPaymentGateway,
  DisabledSmsGateway,
  EmailGateway,
  MeetingProvider,
  PaymentGateway,
  SmsGateway,
} from './provider.interfaces';
import { PaystackGateway } from './paystack.gateway';
import { FlutterwaveGateway } from './flutterwave.gateway';
import { AfricasTalkingGateway } from './africas-talking.gateway';
import { ResendEmailGateway } from './resend.email.gateway';
import { CpanelEmailGateway } from './cpanel.email.gateway';
import { GoogleMeetGateway } from './google-meet.gateway';
import { ZoomGateway } from './zoom.gateway';
import { StubAiGateway } from './stub-ai.gateway';
import { OpenAiGateway } from './openai.gateway';
import { GeminiGateway } from './gemini.gateway';
import { KiloGateway } from './kilo.gateway';
import { OpayGateway } from './opay.gateway';
import { PalmPayGateway } from './palmpay.gateway';
import { MomoGateway } from './momo.gateway';
import {
  DisabledPushGateway,
  PushGateway,
  StubPushGateway,
} from './push.gateway';

/**
 * Resolves the active adapter for a capability from control runtime config.
 */
@Injectable()
export class ProviderRegistryService implements OnModuleInit {
  private readonly logger = new Logger(ProviderRegistryService.name);
  private readonly paymentAdapters = new Map<string, PaymentGateway>();
  private readonly smsAdapters = new Map<string, SmsGateway>();
  private readonly emailAdapters = new Map<string, EmailGateway>();
  private readonly aiAdapters = new Map<string, AiProvider>();
  private readonly meetingAdapters = new Map<string, MeetingProvider>();
  private readonly pushAdapters = new Map<string, PushGateway>();

  constructor(
    private readonly runtimeConfig: RuntimeConfigService,
    private readonly secrets: ProviderSecretsService,
    private readonly paystack: PaystackGateway,
    private readonly flutterwave: FlutterwaveGateway,
    private readonly africasTalking: AfricasTalkingGateway,
    private readonly resendEmail: ResendEmailGateway,
    private readonly cpanelEmail: CpanelEmailGateway,
    private readonly googleMeet: GoogleMeetGateway,
    private readonly zoom: ZoomGateway,
    private readonly stubAi: StubAiGateway,
    private readonly openAi: OpenAiGateway,
    private readonly gemini: GeminiGateway,
    private readonly kilo: KiloGateway,
    private readonly opay: OpayGateway,
    private readonly palmpay: PalmPayGateway,
    private readonly momo: MomoGateway,
    private readonly stubPush: StubPushGateway,
  ) {}

  onModuleInit() {
    this.paymentAdapters.set('disabled', new DisabledPaymentGateway());
    this.paymentAdapters.set('paystack', this.paystack);
    this.paymentAdapters.set('flutterwave', this.flutterwave);
    this.paymentAdapters.set('opay', this.opay);
    this.paymentAdapters.set('palmpay', this.palmpay);
    this.paymentAdapters.set('momo', this.momo);
    this.smsAdapters.set('disabled', new DisabledSmsGateway());
    this.smsAdapters.set('africas_talking', this.africasTalking);
    this.emailAdapters.set('disabled', new DisabledEmailGateway());
    this.emailAdapters.set('resend', this.resendEmail);
    this.emailAdapters.set('cpanel', this.cpanelEmail);
    this.aiAdapters.set('disabled', new DisabledAiProvider());
    this.aiAdapters.set('stub_faq', this.stubAi);
    this.aiAdapters.set('openai', this.openAi);
    this.aiAdapters.set('gemini', this.gemini);
    this.aiAdapters.set('kilo', this.kilo);
    // Alias: operators may select "nvidia" when routing via Kilo for NVIDIA models.
    this.aiAdapters.set('nvidia', this.kilo);
    this.meetingAdapters.set('disabled', new DisabledMeetingProvider());
    this.meetingAdapters.set('google_meet', this.googleMeet);
    this.meetingAdapters.set('zoom', this.zoom);
    this.pushAdapters.set('disabled', new DisabledPushGateway());
    this.pushAdapters.set('fcm', this.stubPush);
    this.pushAdapters.set('onesignal', this.stubPush);
    this.logger.log(
      'Provider adapters: paystack, flutterwave, opay, palmpay, momo, africas_talking, resend, cpanel, google_meet, zoom, openai, gemini, kilo, stub_faq, fcm',
    );
  }

  registerPayment(adapter: PaymentGateway) {
    this.paymentAdapters.set(adapter.id, adapter);
  }
  registerSms(adapter: SmsGateway) {
    this.smsAdapters.set(adapter.id, adapter);
  }
  registerEmail(adapter: EmailGateway) {
    this.emailAdapters.set(adapter.id, adapter);
  }
  registerAi(adapter: AiProvider) {
    this.aiAdapters.set(adapter.id, adapter);
  }
  registerMeeting(adapter: MeetingProvider) {
    this.meetingAdapters.set(adapter.id, adapter);
  }
  registerPush(adapter: PushGateway) {
    this.pushAdapters.set(adapter.id, adapter);
  }

  async resolvePayment(tenantId: string): Promise<PaymentGateway> {
    return this.resolve(
      tenantId,
      'payment',
      this.paymentAdapters,
      new DisabledPaymentGateway(),
    );
  }

  async resolveSms(tenantId: string): Promise<SmsGateway> {
    const gw = await this.resolve(
      tenantId,
      'sms',
      this.smsAdapters,
      new DisabledSmsGateway(),
    );
    if (gw instanceof AfricasTalkingGateway) {
      return gw.bindTenant(tenantId);
    }
    return gw;
  }

  async resolveEmail(tenantId: string): Promise<EmailGateway> {
    if (tenantId === '__platform__') {
      return this.resolveEmailForOnboarding();
    }
    const gw = await this.resolve(
      tenantId,
      'email',
      this.emailAdapters,
      new DisabledEmailGateway(),
    );
    return this.bindEmailTenant(gw, tenantId);
  }

  async resolveEmailForOnboarding(): Promise<EmailGateway> {
    // Pre-tenant flows read the global email adapter from the control vault
    // without auto-registering a synthetic tenant in runtime config.
    const payload = await this.secrets.getSecrets('__platform__', 'email');
    const providerId = payload?.providerId;
    if (!providerId) {
      return new DisabledEmailGateway();
    }
    const gw = this.emailAdapters.get(providerId);
    if (!gw || gw.id === 'disabled') {
      this.logger.warn(
        `Platform email provider '${providerId}' is not registered`,
      );
      return new DisabledEmailGateway();
    }
    return this.bindEmailTenant(gw, '__platform__');
  }

  private bindEmailTenant(gw: EmailGateway, tenantId: string): EmailGateway {
    if (
      gw instanceof ResendEmailGateway ||
      gw instanceof CpanelEmailGateway
    ) {
      return gw.bindTenant(tenantId);
    }
    return gw;
  }

  async resolveAi(tenantId: string): Promise<AiProvider> {
    return this.resolve(tenantId, 'ai', this.aiAdapters, new DisabledAiProvider());
  }

  async resolveMeeting(tenantId: string): Promise<MeetingProvider> {
    return this.resolve(
      tenantId,
      'meeting',
      this.meetingAdapters,
      new DisabledMeetingProvider(),
    );
  }

  async resolvePush(tenantId: string): Promise<PushGateway> {
    return this.resolve(tenantId, 'push', this.pushAdapters, new DisabledPushGateway());
  }

  async getProviderStatus(tenantId: string): Promise<
    Record<string, { providerId: string; mode?: string; adapterRegistered: boolean }>
  > {
    const config = await this.runtimeConfig.getConfig(tenantId);
    const out: Record<
      string,
      { providerId: string; mode?: string; adapterRegistered: boolean }
    > = {};
    for (const [cap, entry] of Object.entries(config.providers || {})) {
      if (!entry) continue;
      const map =
        cap === 'payment'
          ? this.paymentAdapters
          : cap === 'sms'
            ? this.smsAdapters
            : cap === 'email'
              ? this.emailAdapters
              : cap === 'ai'
                ? this.aiAdapters
                : cap === 'meeting'
                  ? this.meetingAdapters
                  : cap === 'push'
                    ? this.pushAdapters
                    : null;
      out[cap] = {
        providerId: entry.providerId,
        mode: entry.mode,
        adapterRegistered: map ? map.has(entry.providerId) : false,
      };
    }
    return out;
  }

  private async resolve<T extends { id: string }>(
    tenantId: string,
    capability: string,
    adapters: Map<string, T>,
    fallback: T,
  ): Promise<T> {
    const config = await this.runtimeConfig.getConfig(tenantId);
    const entry = (config.providers as any)?.[capability];
    if (!entry?.providerId) {
      this.logger.debug(`No ${capability} provider for tenant ${tenantId}`);
      return fallback;
    }
    const adapter = adapters.get(entry.providerId);
    if (!adapter) {
      this.logger.warn(
        `Provider ${entry.providerId} for ${capability} not registered; using disabled`,
      );
      return fallback;
    }
    return adapter;
  }
}
