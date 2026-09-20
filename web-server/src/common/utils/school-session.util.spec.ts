import {
  hasVerificationWindowConfigured,
  userRequiresDailyVerification,
} from './school-session.util';

describe('staff verification configuration', () => {
  it('does not require verification when the tenant has no window configured', () => {
    expect(
      hasVerificationWindowConfigured({
      }),
    ).toBe(false);
    expect(
      userRequiresDailyVerification(['class_teacher'], {
        verificationRequiredRoles: ['class_teacher'],
      }),
    ).toBe(false);
  });

  it('requires configured staff roles when both window bounds exist', () => {
    expect(
      userRequiresDailyVerification(['class_teacher'], {
        biometricWindowStart: '08:00',
        biometricWindowEnd: '16:00',
        verificationRequiredRoles: ['class_teacher'],
      }),
    ).toBe(true);
  });

  it('does not treat a partial window as configured', () => {
    expect(
      hasVerificationWindowConfigured({
        biometricWindowStart: '08:00',
      }),
    ).toBe(false);
  });
});
