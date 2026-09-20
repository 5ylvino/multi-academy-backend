import {
  IsArray,
  IsBoolean,
  IsEmail,
  IsIn,
  IsInt,
  IsNumber,
  IsObject,
  IsOptional,
  IsString,
  IsUrl,
  Max,
  Min,
  MinLength,
  ValidateNested,
} from 'class-validator';
import { Type } from 'class-transformer';

export class StaffVerificationLocationDto {
  @IsString()
  @MinLength(1)
  id!: string;

  @IsString()
  @MinLength(1)
  name!: string;

  @IsNumber()
  latitude!: number;

  @IsNumber()
  longitude!: number;

  @IsNumber()
  radiusMeters!: number;
}

export class AttendanceConfigDto {
  @IsOptional()
  @IsBoolean()
  requireLocationVerification?: boolean;

  @IsOptional()
  @IsArray()
  @ValidateNested({ each: true })
  @Type(() => StaffVerificationLocationDto)
  locations?: StaffVerificationLocationDto[];

  /** Daily session start (HH:mm), e.g. "08:00". */
  @IsOptional()
  @IsString()
  sessionStartTime?: string;

  /** Daily session end (HH:mm), e.g. "16:00". */
  @IsOptional()
  @IsString()
  sessionEndTime?: string;

  /** Biometric verification window start (HH:mm). Defaults to sessionStartTime. */
  @IsOptional()
  @IsString()
  biometricWindowStart?: string;

  /** Biometric verification window end (HH:mm). Defaults to sessionEndTime. */
  @IsOptional()
  @IsString()
  biometricWindowEnd?: string;

  /** Business days (0=Sun … 6=Sat). Default Mon–Fri. */
  @IsOptional()
  @IsArray()
  @IsInt({ each: true })
  @Min(0, { each: true })
  @Max(6, { each: true })
  businessDays?: number[];

  /** One-time 20-digit manual verification code (server-managed). */
  @IsOptional()
  @IsString()
  manualVerificationCode?: string | null;

  @IsOptional()
  @IsString()
  manualCodeCreatedAt?: string | null;

  /** Roles that must complete daily biometric/manual verification after login. */
  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  verificationRequiredRoles?: string[];

  /** Tracks last calendar day unverified staff were auto-marked absent. */
  @IsOptional()
  @IsString()
  lastStaffFinalizeDate?: string | null;
}

export class LoginPageConfigDto {
  @IsOptional()
  @IsBoolean()
  enabled?: boolean;

  @IsOptional()
  @IsString()
  welcomeMessage?: string;

  @IsOptional()
  @IsString()
  subtitle?: string;

  @IsOptional()
  @IsIn(['solid', 'gradient'])
  bgType?: 'solid' | 'gradient';

  @IsOptional()
  @IsString()
  bgColor?: string;

  @IsOptional()
  @IsString()
  bgGradientTo?: string;

  @IsOptional()
  @IsBoolean()
  showMotto?: boolean;

  @IsOptional()
  @IsString()
  footerText?: string;
}

export class CreateOrganizationDto {
  @IsString()
  @MinLength(2)
  name!: string;

  @IsEmail()
  email!: string;

  @IsOptional()
  @IsString()
  phone?: string;

  @IsOptional()
  @IsString()
  address?: string;

  @IsOptional()
  @IsString()
  country?: string;

  @IsOptional()
  @IsString()
  state?: string;

  @IsOptional()
  @IsString()
  city?: string;

  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  @IsIn(['primary', 'secondary', 'nursery'], { each: true })
  schoolLevels?: string[];
}

export class UpdateOrganizationDto {
  @IsOptional()
  @IsString()
  @MinLength(2)
  name?: string;

  @IsOptional()
  @IsEmail()
  email?: string;

  @IsOptional()
  @IsString()
  phone?: string;

  @IsOptional()
  @IsString()
  address?: string;

  @IsOptional()
  @IsString()
  country?: string;

  @IsOptional()
  @IsString()
  state?: string;

  @IsOptional()
  @IsString()
  city?: string;

  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  @IsIn(['primary', 'secondary', 'nursery'], { each: true })
  schoolLevels?: string[];

  @IsOptional()
  @IsString()
  motto?: string | null;

  @IsOptional()
  @IsString()
  website?: string | null;

  /** School logo as a data-URL (image/* base64). */
  @IsOptional()
  @IsString()
  logo?: string | null;

  @IsOptional()
  @IsString()
  brandColor?: string | null;

  @IsOptional()
  @IsObject()
  @ValidateNested()
  @Type(() => LoginPageConfigDto)
  loginPageConfig?: LoginPageConfigDto | null;

  @IsOptional()
  @IsObject()
  @ValidateNested()
  @Type(() => AttendanceConfigDto)
  attendanceConfig?: AttendanceConfigDto | null;
}

export class ChangePlanDto {
  @IsString()
  planId!: string;

  @IsIn(['monthly', 'yearly'])
  billingCycle!: 'monthly' | 'yearly';

  @IsOptional()
  @IsUrl({ require_tld: false })
  callbackUrl?: string;
}

export class CreateSaasCheckoutDto {
  @IsEmail()
  email!: string;

  @IsUrl({ require_tld: false })
  callbackUrl!: string;
}
