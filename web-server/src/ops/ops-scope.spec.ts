import { canManageClinicRole } from './clinic/clinic.service';
import { isTransportManagerRole } from './transport/transport.service';

describe('operations role ownership', () => {
  it('keeps transport management with school operations leaders', () => {
    expect(isTransportManagerRole('school_admin')).toBe(true);
    expect(isTransportManagerRole('driver')).toBe(false);
    expect(isTransportManagerRole('parent')).toBe(false);
  });

  it('keeps clinic writes with clinic staff and school leadership', () => {
    expect(canManageClinicRole('nurse')).toBe(true);
    expect(canManageClinicRole('principal')).toBe(true);
    expect(canManageClinicRole('teacher')).toBe(false);
    expect(canManageClinicRole('parent')).toBe(false);
  });
});
