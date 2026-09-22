import { Body, Controller, Get, Headers, Post, Query, UnauthorizedException } from '@nestjs/common';
import { Throttle } from '@nestjs/throttler';
import { ok } from '../common/types/api-response';
import { AuthService } from './auth.service';
import { GoogleOauthService } from './google-oauth.service';
import { RegisterStep1Dto } from './dto/register-step1.dto';
import { RegisterStep2Dto } from './dto/register-step2.dto';
import { LoginDto } from './dto/login.dto';
import { RefreshDto } from './dto/refresh.dto';
import { PasswordResetConfirmDto, PasswordResetRequestDto } from './dto/password-reset.dto';
import { Public } from '../common/auth/public.decorator';
import { VerifyEmailDto } from './dto/verify-email.dto';
import { ResendVerificationDto } from './dto/resend-verification.dto';

@Controller('auth')
export class AuthController {
  constructor(
    private readonly authService: AuthService,
    private readonly googleOauth: GoogleOauthService,
  ) {}

  @Post('register/step-1')
  @Public()
  @Throttle({ default: { limit: 5, ttl: 60_000 } })
  async registerStep1(@Body() body: RegisterStep1Dto) {
    const data = await this.authService.registerStep1(body);
    return ok(
      'Registration initiated. Check your email to verify ownership and continue onboarding.',
      data,
    );
  }

  @Post('register/verify-email')
  @Public()
  @Throttle({ default: { limit: 5, ttl: 60_000 } })
  async verifyEmail(@Body() body: VerifyEmailDto) {
    const data = await this.authService.verifyEmail(body.token);
    return ok('Email verified and school onboarding completed', data);
  }

  @Post('register/resend-verification')
  @Public()
  @Throttle({ default: { limit: 3, ttl: 60_000 } })
  async resendVerification(@Body() body: ResendVerificationDto) {
    const data = await this.authService.resendVerificationEmail(body.email);
    return ok('If eligible, a new verification email has been sent', data);
  }

  @Post('register/step-2')
  @Public()
  @Throttle({ default: { limit: 5, ttl: 60_000 } })
  async registerStep2(@Body() body: RegisterStep2Dto) {
    const data = await this.authService.registerStep2(body);
    return ok('Primary owner created and assigned all roles/permissions', data);
  }

  @Post('login')
  @Public()
  @Throttle({ default: { limit: 10, ttl: 60_000 } })
  async login(@Body() body: LoginDto) {
    const data = await this.authService.login(body);
    return ok('Login successful', data);
  }

  @Get('me')
  async me(@Headers('authorization') authorization?: string) {
    const token = extractBearer(authorization);
    const data = await this.authService.me(token);
    return ok('Current user profile', data);
  }

  @Post('refresh')
  @Public()
  @Throttle({ default: { limit: 20, ttl: 60_000 } })
  async refresh(@Body() body: RefreshDto) {
    const data = await this.authService.refresh(body.refresh_token);
    return ok('Token refreshed', data);
  }

  @Post('password-reset/request')
  @Public()
  @Throttle({ default: { limit: 5, ttl: 60_000 } })
  async passwordResetRequest(@Body() body: PasswordResetRequestDto) {
    const data = await this.authService.requestPasswordReset(body.email, body.schoolSlug);
    return ok(data.message, data);
  }

  @Post('password-reset/confirm')
  @Public()
  @Throttle({ default: { limit: 5, ttl: 60_000 } })
  async passwordResetConfirm(@Body() body: PasswordResetConfirmDto) {
    const data = await this.authService.confirmPasswordReset(body.token, body.password);
    return ok(data.message, data);
  }

  @Get('google/start')
  @Public()
  @Throttle({ default: { limit: 10, ttl: 60_000 } })
  async googleStart(
    @Query('redirectUri') redirectUri: string,
    @Query('tenantId') tenantId?: string,
  ) {
    const data = await this.googleOauth.start(
      tenantId,
      redirectUri || `${process.env.CLIENT_APP_URL || 'http://localhost:3000'}/login/google/callback`,
    );
    return ok('Google OAuth start', data);
  }

  @Post('google')
  @Public()
  @Throttle({ default: { limit: 10, ttl: 60_000 } })
  async googleCallback(
    @Body() body: { code: string; redirectUri: string; tenantId?: string },
  ) {
    const data = await this.googleOauth.callback({
      code: body.code,
      redirectUri: body.redirectUri,
      tenantId: body.tenantId,
    });
    return ok('Google OAuth callback', data);
  }
}

function extractBearer(authorization?: string): string {
  if (!authorization) {
    throw new UnauthorizedException('Missing Authorization header');
  }
  const [scheme, token] = authorization.split(' ');
  if (scheme !== 'Bearer' || !token) {
    throw new UnauthorizedException('Invalid Authorization header');
  }
  return token;
}
