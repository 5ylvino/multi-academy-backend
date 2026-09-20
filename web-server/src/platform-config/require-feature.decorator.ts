import { SetMetadata } from '@nestjs/common';

export const FEATURE_KEYS = 'feature_keys';

/** Require at least one of the listed feature flags to be enabled for the tenant. */
export const RequireFeature = (...featureKeys: string[]) =>
  SetMetadata(FEATURE_KEYS, featureKeys);
