export interface AuthUserClaims {
  sub: string;
  user_id: string;
  tenant_id: string;
  email: string;
  roles: string[];
  permissions: string[];
  capabilities: string[];
  user_details?: {
    email: string;
    roles: string[];
    permissions: string[];
    capabilities: string[];
  };
  /** Control-plane config version embedded at login — enables guard fast path. */
  config_version?: number;
  tenant_status?: string;
  features_digest?: string;
  [key: string]: unknown;
}
