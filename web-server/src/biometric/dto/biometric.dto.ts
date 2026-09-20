import {
  IsIn,
  IsObject,
  IsOptional,
  IsString,
  MinLength,
} from 'class-validator';

export class BiometricRegisterVerifyDto {
  @IsObject()
  response!: Record<string, unknown>;
}

export class BiometricAuthVerifyDto {
  @IsObject()
  response!: Record<string, unknown>;

  /** Optional purpose tag for audit / payment binding */
  @IsOptional()
  @IsString()
  @MinLength(1)
  @IsIn(['payment', 'staff_attendance', 'staff_login', 'general'])
  purpose?: string;
}

export class ConsumeBiometricAssertionDto {
  @IsString()
  @MinLength(1)
  assertionId!: string;

  @IsIn(['payment', 'staff_attendance', 'staff_login', 'general'])
  purpose!: 'payment' | 'staff_attendance' | 'staff_login' | 'general';
}
