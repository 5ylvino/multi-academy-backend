import {
  BadRequestException,
  Body,
  Controller,
  ForbiddenException,
  Get,
  ServiceUnavailableException,
  Param,
  Patch,
  Post,
  Query,
} from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { RuntimeConfigService } from '../platform-config/runtime-config.service';
import { PaymentServiceClient } from '../payments/payment-service.client';
import { TutoringServiceClient } from './tutoring-service.client';

@Controller('tutoring')
export class TutoringController {
  constructor(
    private readonly tutoring: TutoringServiceClient,
    private readonly flags: FeatureFlagService,
    private readonly paymentClient: PaymentServiceClient,
    private readonly runtimeConfig: RuntimeConfigService,
  ) {}

  private actor(user?: AuthUserClaims) {
    const roles = Array.isArray(user?.roles)
      ? user.roles.map((r) => String(r).toLowerCase())
      : user?.role
        ? [String(user.role).toLowerCase()]
        : [];
    return { userId: user?.user_id || user?.sub || 'system', roles };
  }

  @Post('tutors/register')
  @RequireFeature('tutoring.marketplace')
  @RequirePermissions('classes:read')
  async registerTutor(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body()
    body: {
      displayName: string;
      bio?: string;
      subjects?: string[];
      hourlyRate?: number;
      visibility?: string;
      isExternal?: boolean;
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'tutoring.marketplace');
    if (!this.tutoring.isEnabled()) throw new ForbiddenException('TUTORING_SERVICE_URL not configured');
    const result = await this.tutoring.post(
      '/v1/tutors/register',
      tenantId,
      this.actor(user),
      body,
      ['tutoring.marketplace'],
    );
    return ok('Tutor profile saved', result);
  }

  @Get('tutors/search')
  @RequireFeature('tutoring.marketplace')
  async searchTutors(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('subject') subject?: string,
    @Query('includeExternal') includeExternal?: string,
  ) {
    await this.flags.assertEnabled(tenantId, 'tutoring.marketplace');
    const config = await this.runtimeConfig.getConfig(tenantId);
    if (config.tutoring?.marketplaceEnabled === false) {
      return ok('Tutors', { items: [], total: 0 });
    }
    if (!this.tutoring.isEnabled()) return ok('Tutors', { items: [], total: 0 });
    const q = new URLSearchParams();
    if (subject) q.set('subject', subject);
    const resolvedIncludeExternal =
      includeExternal !== undefined
        ? includeExternal
        : config.tutoring?.allowExternalTutors !== false
          ? 'true'
          : 'false';
    q.set('includeExternal', resolvedIncludeExternal);
    const result = await this.tutoring.get(
      `/v1/tutors/search?${q.toString()}`,
      tenantId,
      this.actor(user),
      ['tutoring.marketplace'],
    );
    return ok('Tutors', result);
  }

  @Get('tutors/me')
  @RequireFeature('tutoring.marketplace')
  async myProfile(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    await this.flags.assertEnabled(tenantId, 'tutoring.marketplace');
    if (!this.tutoring.isEnabled()) throw new ForbiddenException('TUTORING_SERVICE_URL not configured');
    const result = await this.tutoring.get('/v1/tutors/me', tenantId, this.actor(user), ['tutoring.marketplace']);
    return ok('My tutor profile', result);
  }

  @Patch('tutors/:id')
  @RequireFeature('tutoring.marketplace')
  @RequirePermissions('classes:read')
  async updateTutor(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body()
    body: {
      displayName?: string;
      bio?: string;
      subjects?: string[];
      hourlyRate?: number;
      visibility?: string;
    },
  ) {
    if (!id?.trim()) throw new BadRequestException('tutor id required');
    await this.flags.assertEnabled(tenantId, 'tutoring.marketplace');
    if (!this.tutoring.isEnabled()) throw new ForbiddenException('TUTORING_SERVICE_URL not configured');
    const result = await this.tutoring.patch(
      `/v1/tutors/${encodeURIComponent(id)}`,
      tenantId,
      this.actor(user),
      body,
      ['tutoring.marketplace'],
    );
    return ok('Tutor profile updated', result);
  }

  @Get('bookings/mine')
  @RequireFeature('tutoring.marketplace')
  async myBookings(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims) {
    await this.flags.assertEnabled(tenantId, 'tutoring.marketplace');
    if (!this.tutoring.isEnabled()) return ok('Bookings', { items: [], total: 0 });
    const result = await this.tutoring.get('/v1/bookings/mine', tenantId, this.actor(user), [
      'tutoring.marketplace',
    ]);
    return ok('Bookings', result);
  }

  @Post('bookings')
  @RequireFeature('tutoring.marketplace')
  async createBooking(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: Record<string, unknown>,
  ) {
    await this.flags.assertEnabled(tenantId, 'tutoring.marketplace');
    if (!this.tutoring.isEnabled()) throw new ForbiddenException('TUTORING_SERVICE_URL not configured');
    const result = await this.tutoring.post('/v1/bookings', tenantId, this.actor(user), body, [
      'tutoring.marketplace',
    ]);
    return ok('Booking created', result);
  }

  @Post('bookings/:id/confirm')
  @RequireFeature('tutoring.marketplace')
  @RequirePermissions('classes:read')
  async confirmBooking(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
  ) {
    if (!id?.trim()) throw new BadRequestException('booking id required');
    await this.flags.assertEnabled(tenantId, 'tutoring.marketplace');
    if (!this.tutoring.isEnabled()) throw new ForbiddenException('TUTORING_SERVICE_URL not configured');
    const result = await this.tutoring.post(
      `/v1/bookings/${encodeURIComponent(id)}/confirm`,
      tenantId,
      this.actor(user),
      {},
      ['tutoring.marketplace'],
    );
    return ok('Booking confirmed', result);
  }

  @Post('payments/intent')
  @RequireFeature('tutoring.payments')
  async paymentIntent(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { bookingId: string },
  ) {
    if (!body.bookingId?.trim()) throw new BadRequestException('bookingId required');
    await this.flags.assertEnabled(tenantId, 'tutoring.payments');
    if (!this.tutoring.isEnabled()) throw new ForbiddenException('TUTORING_SERVICE_URL not configured');
    const result = await this.tutoring.post(
      '/v1/payments/intent',
      tenantId,
      this.actor(user),
      body,
      ['tutoring.payments'],
    );
    return ok('Payment intent', result);
  }

  @Post('payments/checkout')
  @RequireFeature('tutoring.payments')
  async paymentCheckout(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body()
    body: {
      bookingId: string;
      email: string;
      callbackUrl: string;
    },
  ) {
    if (!body.bookingId?.trim()) throw new BadRequestException('bookingId required');
    if (!body.email?.trim()) throw new BadRequestException('email required');
    if (!body.callbackUrl?.trim()) throw new BadRequestException('callbackUrl required');
    await this.flags.assertEnabled(tenantId, 'tutoring.payments');
    if (!this.tutoring.isEnabled()) throw new ForbiddenException('TUTORING_SERVICE_URL not configured');

    const intent = (await this.tutoring.post(
      '/v1/payments/intent',
      tenantId,
      this.actor(user),
      { bookingId: body.bookingId },
      ['tutoring.payments'],
    )) as {
      paymentId?: string;
      amount?: number;
      currency?: string;
    };

    if (!intent?.paymentId || !Number.isFinite(Number(intent.amount))) {
      throw new BadRequestException('Could not create tutoring payment intent');
    }

    if (!this.paymentClient.isEnabled()) {
      if (process.env.NODE_ENV === 'production') {
        throw new ServiceUnavailableException(
          'Online payment checkout is not available. Contact your school administrator.',
        );
      }
      return ok('Payment intent (development mode)', intent);
    }

    const amountMinor = Math.round(Number(intent.amount) * 100);
    const checkout = await this.paymentClient.createCheckout(
      tenantId,
      this.actor(user),
      {
        context: 'tutoring_session',
        amountMinor,
        email: body.email.trim(),
        callbackUrl: body.callbackUrl.trim(),
        currency: intent.currency || 'NGN',
        metadata: {
          tenantId,
          bookingId: body.bookingId,
          paymentId: intent.paymentId,
          contextKey: 'tutoring_session',
        },
      },
      ['tutoring.payments'],
    );

    return ok('Tutoring checkout started', {
      ...checkout,
      paymentId: intent.paymentId,
      amount: intent.amount,
      currency: intent.currency || 'NGN',
    });
  }

  @Post('payments/verify')
  @RequireFeature('tutoring.payments')
  async verifyPayment(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { reference: string },
  ) {
    if (!body.reference?.trim()) throw new BadRequestException('reference required');
    await this.flags.assertEnabled(tenantId, 'tutoring.payments');
    if (!this.tutoring.isEnabled()) throw new ForbiddenException('TUTORING_SERVICE_URL not configured');

    if (!this.paymentClient.isEnabled()) {
      throw new ServiceUnavailableException(
        'Online payment checkout is not available. Contact your school administrator.',
      );
    }

    const verified = await this.paymentClient.verify(
      tenantId,
      this.actor(user),
      body.reference.trim(),
      ['tutoring.payments'],
    );

    if (verified.status !== 'success') {
      return ok('Payment not completed', { status: verified.status });
    }

    const paymentId = String(
      verified.metadata?.paymentId || verified.metadata?.payment_id || '',
    );
    if (!paymentId) {
      throw new BadRequestException('Tutoring payment id missing from checkout metadata');
    }

    const settled = await this.tutoring.post(
      `/v1/payments/${encodeURIComponent(paymentId)}/settle`,
      tenantId,
      this.actor(user),
      {
        providerReference: verified.providerReference || body.reference,
        checkoutReference: body.reference.trim(),
      },
      ['tutoring.payments'],
    );

    return ok('Tutoring payment settled', {
      verify: verified,
      payment: settled,
    });
  }

  @Post('sessions/:bookingId/ai-prep')
  @RequireFeature('tutoring.ai_hybrid')
  async aiPrep(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('bookingId') bookingId: string,
  ) {
    await this.flags.assertEnabled(tenantId, 'tutoring.ai_hybrid');
    if (!this.tutoring.isEnabled()) throw new ForbiddenException('TUTORING_SERVICE_URL not configured');
    const result = await this.tutoring.post(
      `/v1/sessions/${encodeURIComponent(bookingId)}/ai-prep`,
      tenantId,
      this.actor(user),
      {},
      ['tutoring.ai_hybrid', 'ai.tutor'],
    );
    return ok('AI prep session', result);
  }
}
