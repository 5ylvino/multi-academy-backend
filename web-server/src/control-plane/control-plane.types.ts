export type TenantStatus = 'pending_verification' | 'active' | 'suspended' | 'deleted';

export interface TenantConfig {
  id: string;
  slug: string;
  schoolBusinessOrganisationId: string;
  name: string;
  status: TenantStatus;
  dbName: string;
  dbUri: string;
  schoolLevels: string[];
  onboardingToken?: string;
  onboardingTokenExpiresAt?: string;
  ownerRegistrationPayloadJson?: Record<string, any>;
  ownerStagedPasswordHash?: string;
  ownerEmailVerified?: boolean;
  provisioningStatus: 'pending' | 'ready' | 'failed';
  provisioningError?: string;
  createdAt: string;
  updatedAt: string;
}

export interface UserTenantMapping {
  id: string;
  userId: string;
  tenantId: string;
  email: string;
  isPrimaryTenant: boolean;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface TenantUser {
  id: string;
  tenantId: string;
  email: string;
  name: string;
  phone?: string | null;
  schoolLevel?: string | null;
  passwordHash: string;
  roles: string[];
  permissions: string[];
  capabilities: string[];
  isPrimaryOwner: boolean;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

