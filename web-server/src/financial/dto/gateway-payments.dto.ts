import {
  ArrayMinSize,
  IsArray,
  IsEmail,
  IsIn,
  IsNumber,
  IsOptional,
  IsString,
  IsUrl,
  Min,
  MinLength,
  ValidateNested,
} from 'class-validator';
import { Type } from 'class-transformer';

export class CreateGatewayCheckoutDto {
  @IsString()
  @MinLength(1)
  studentId!: string;

  @IsOptional()
  @IsString()
  invoiceId?: string;

  @IsOptional()
  @IsString()
  installmentId?: string;

  @IsNumber()
  @Min(0.01)
  amount!: number;

  @IsEmail()
  email!: string;

  @IsUrl({ require_tld: false, protocols: ['http', 'https', 'masms'] })
  callbackUrl!: string;

  @IsOptional()
  @IsIn(['invoice', 'advance', 'installment', 'saas_subscription'])
  kind?: 'invoice' | 'advance' | 'installment' | 'saas_subscription';

  @IsOptional()
  @IsString()
  term?: string;

  @IsOptional()
  @IsString()
  sessionLabel?: string;
}

export class CreateAdvanceCheckoutDto {
  @IsString()
  @MinLength(1)
  studentId!: string;

  @IsNumber()
  @Min(0.01)
  amount!: number;

  @IsEmail()
  email!: string;

  @IsUrl({ require_tld: false, protocols: ['http', 'https', 'masms'] })
  callbackUrl!: string;

  @IsOptional()
  @IsString()
  term?: string;

  @IsOptional()
  @IsString()
  sessionLabel?: string;
}

export class VerifyGatewayPaymentDto {
  @IsString()
  @MinLength(1)
  reference!: string;
}

class InstallmentRowDto {
  @IsNumber()
  @Min(0.01)
  amount!: number;

  @IsOptional()
  @IsString()
  dueDate?: string;
}

export class CreateInstallmentPlanDto {
  @IsString()
  @MinLength(1)
  studentId!: string;

  @IsOptional()
  @IsString()
  invoiceId?: string;

  @IsString()
  @MinLength(1)
  title!: string;

  @IsNumber()
  @Min(0.01)
  totalAmount!: number;

  @IsOptional()
  @IsNumber()
  @Min(0)
  surchargePercent?: number;

  @IsArray()
  @ArrayMinSize(1)
  @ValidateNested({ each: true })
  @Type(() => InstallmentRowDto)
  installments!: InstallmentRowDto[];
}
