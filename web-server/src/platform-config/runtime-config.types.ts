/** ControlConfig v1 — matches FastAPI TenantRuntimeConfig. */

export type TenantStatus =
  | 'active'
  | 'suspended'
  | 'restricted'
  | 'blacklisted'
  | 'provisioning'
  | 'archived';

export type ProviderCapability =
  | 'payment'
  | 'sms'
  | 'email'
  | 'ai'
  | 'meeting'
  | 'storage'
  | 'push'
  | 'maps';

export interface FeeSplitAllocation {
  destination: string;
  percentage_bps: number;
}

export interface FeeSplitRule {
  feeType: string;
  providerId: string;
  currency?: string;
  allocations: FeeSplitAllocation[];
}

export interface PaymentContextSetting {
  contextKey: string;
  label: string;
  description: string;
  enabled: boolean;
  buttonEnabled: boolean;
  featuresSatisfied: boolean;
  requiredFeatures: string[];
  gatewayId?: string | null;
  allowedGateways: string[];
}

export interface PaymentGatewaySetting {
  gatewayId: string;
  label: string;
  enabled: boolean;
}

export interface TutoringPolicyConfig {
  platformFeePercent: number;
  defaultCurrency: string;
  allowExternalTutors: boolean;
  marketplaceEnabled: boolean;
}

export interface TenantRuntimeConfig {
  tenantId: string;
  slug?: string;
  feeSplits?: FeeSplitRule[];
  paymentSettings?: {
    contexts: PaymentContextSetting[];
    gateways: PaymentGatewaySetting[];
  };
  tutoring?: TutoringPolicyConfig;
  subscription: {
    type: 'catalog' | 'custom';
    planId?: string | null;
    dealId?: string | null;
    billingCycle: string;
    status?: string;
    trial?: {
      enabled: boolean;
      startAt?: string | null;
      endAt?: string | null;
      daysRemaining?: number | null;
    } | null;
  } | null;
  status: TenantStatus;
  features: Record<string, boolean>;
  quotas: Record<string, number>;
  usageRemaining?: Record<string, number>;
  providers: Partial<
    Record<
      ProviderCapability,
      {
        providerId: string;
        mode: 'sandbox' | 'live';
        [key: string]: unknown;
      }
    >
  >;
  enforcement: {
    schoolBlacklisted: boolean;
    blockedUserIds: string[];
    blockedEmails: string[];
    blockedIpCidrs: string[];
    blockedDevices?: string[];
    capabilityFreezes: string[];
    denyLogin: boolean;
    message?: string | null;
  };
  maintenance?: { active: boolean; message?: string; until?: string } | null;
  killSwitches: string[];
  ttlSeconds?: number;
  updatedAt: string;
  configVersion: number;
}

/** Features that fail closed when control plane is unreachable. */
export const FAIL_CLOSED_FEATURES = [
  'fees.gateway',
  'fees.advance_payment',
  'comms.sms',
  'comms.email',
  'comms.push',
  'ai.assistant',
  'ai.support_chatbot',
  'ai.performance_detection',
  'ai.performance_recommendations',
  'ai.risk_analytics',
  'ai.essay_grading',
  'ai.tutor',
  'ai.teacher_copilot',
  'ai.report_comments',
  'ai.timetable_solver',
  'tutoring.marketplace',
  'tutoring.payments',
  'tutoring.ai_hybrid',
  'comms.meetings',
] as const;
