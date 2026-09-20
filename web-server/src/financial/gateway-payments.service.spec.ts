import { BadRequestException, NotFoundException } from '@nestjs/common';
import { GatewayPaymentsService } from './gateway-payments.service';

describe('GatewayPaymentsService invoice checkout safety', () => {
  function createService(invoice: Record<string, unknown> | undefined) {
    const ds = {
      options: { type: 'sqlite' },
      query: jest.fn(async (sql: string) => {
        if (sql.includes('FROM financial_invoices')) {
          return invoice ? [invoice] : [];
        }
        return [];
      }),
    };
    const gateway = {
      id: 'paystack',
      createCheckout: jest.fn(async () => ({
        checkoutUrl: 'https://checkout.example',
        accessCode: 'access',
      })),
    };
    const paymentClient = { isEnabled: jest.fn(() => false) };
    const service = new GatewayPaymentsService(
      { getTenantById: jest.fn(async () => ({ id: 'tenant-1', dbUri: 'db' })) } as never,
      { getOrCreate: jest.fn(async () => ds) } as never,
      { assertEnabled: jest.fn(async () => undefined) } as never,
      { resolvePayment: jest.fn(async () => gateway) } as never,
      {} as never,
      {} as never,
      {} as never,
      {} as never,
      {} as never,
      paymentClient as never,
      {} as never,
    );
    return service;
  }

  const body = {
    studentId: 'student-1',
    invoiceId: 'invoice-1',
    amount: 100,
    email: 'parent@example.com',
    callbackUrl: 'https://school.example/payment',
  };

  it('rejects an invoice belonging to another student', async () => {
    await expect(
      createService({ studentId: 'student-2', amount: 100, status: 'unpaid' }).createCheckout(
        'tenant-1',
        body,
      ),
    ).rejects.toBeInstanceOf(NotFoundException);
  });

  it('rejects checkout amounts above the invoice total', async () => {
    await expect(
      createService({ studentId: 'student-1', amount: 50, status: 'unpaid' }).createCheckout(
        'tenant-1',
        body,
      ),
    ).rejects.toBeInstanceOf(BadRequestException);
  });
});
