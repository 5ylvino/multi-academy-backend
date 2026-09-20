import { canAdvancePayrollStatus, payrollNet } from './payroll.service';

describe('payroll workflow', () => {
  it('calculates employee net pay from gross, allowances, deductions and PAYE', () => {
    expect(payrollNet(100000, 10000, 5000, 950)).toEqual({ paye: 950, net: 104050 });
  });

  it('defaults PAYE to ten percent of taxable earnings', () => {
    expect(payrollNet(100000, 10000, 0)).toEqual({ paye: 11000, net: 99000 });
  });

  it('only allows draft approval and approved payout preparation', () => {
    expect(canAdvancePayrollStatus('draft', 'approved')).toBe(true);
    expect(canAdvancePayrollStatus('approved', 'payout_ready')).toBe(true);
    expect(canAdvancePayrollStatus('draft', 'payout_ready')).toBe(false);
    expect(canAdvancePayrollStatus('payout_ready', 'approved')).toBe(false);
  });
});
