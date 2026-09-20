import {
  IsBoolean,
  IsIn,
  IsISO8601,
  IsNumber,
  IsOptional,
  IsString,
  Max,
  MaxLength,
  Min,
  MinLength,
  ValidateIf,
} from 'class-validator';

export const FEE_SCHOOL_LEVELS = ['primary', 'secondary', 'nursery'] as const;
export const FEE_TERMS = ['first', 'second', 'third', 'all'] as const;
export const PAYMENT_METHODS = ['cash', 'bank_transfer', 'card', 'other'] as const;
export const PAYMENT_STATUSES = ['completed', 'pending', 'failed'] as const;
export const INVOICE_STATUSES = ['unpaid', 'partial', 'paid', 'cancelled'] as const;
export const INVOICE_PAYMENT_PLAN_TYPES = ['full', 'installment'] as const;
export const SCHOLARSHIP_STATUSES = ['active', 'ended'] as const;
export const REFUND_METHODS = ['cash', 'bank_transfer', 'card', 'other'] as const;
export const REFUND_STATUSES = ['completed', 'pending', 'failed'] as const;

export class CreateFeeStructureDto {
  @IsString()
  @MinLength(1)
  @MaxLength(255)
  name!: string;

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  description?: string;

  @IsIn(FEE_SCHOOL_LEVELS)
  schoolLevel!: (typeof FEE_SCHOOL_LEVELS)[number];

  @IsOptional()
  @IsString()
  classId?: string;

  @IsNumber()
  @Min(0)
  amount!: number;

  @IsIn(FEE_TERMS)
  term!: (typeof FEE_TERMS)[number];

  @IsOptional()
  @IsISO8601()
  dueDate?: string;

  @IsOptional()
  @IsBoolean()
  isActive?: boolean;
}

export class UpdateFeeStructureDto {
  @IsOptional()
  @IsString()
  @MinLength(1)
  @MaxLength(255)
  name?: string;

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  description?: string;

  @IsOptional()
  @IsIn(FEE_SCHOOL_LEVELS)
  schoolLevel?: (typeof FEE_SCHOOL_LEVELS)[number];

  @IsOptional()
  @IsString()
  classId?: string | null;

  @IsOptional()
  @IsNumber()
  @Min(0)
  amount?: number;

  @IsOptional()
  @IsIn(FEE_TERMS)
  term?: (typeof FEE_TERMS)[number];

  @IsOptional()
  @IsISO8601()
  dueDate?: string | null;

  @IsOptional()
  @IsBoolean()
  isActive?: boolean;
}

export class CreatePaymentDto {
  @IsString()
  @MinLength(1)
  studentId!: string;

  @IsNumber()
  @Min(0.01)
  amount!: number;

  @IsIn(PAYMENT_METHODS)
  method!: (typeof PAYMENT_METHODS)[number];

  @IsOptional()
  @IsString()
  @MaxLength(255)
  reference?: string;

  @IsOptional()
  @IsString()
  invoiceId?: string;

  @IsOptional()
  @IsString()
  installmentId?: string;

  @IsISO8601()
  date!: string;

  @IsOptional()
  @IsIn(PAYMENT_STATUSES)
  status?: (typeof PAYMENT_STATUSES)[number];

}

export class CreateInvoiceDto {
  @IsString()
  @MinLength(1)
  studentId!: string;

  @IsOptional()
  @IsString()
  feeStructureId?: string;

  @IsString()
  @MinLength(1)
  @MaxLength(255)
  title!: string;

  @IsNumber()
  @Min(0.01)
  amount!: number;

  @IsIn(FEE_TERMS)
  term!: (typeof FEE_TERMS)[number];

  @IsOptional()
  @IsString()
  @MaxLength(64)
  sessionLabel?: string;

  @IsOptional()
  @IsIn(INVOICE_STATUSES)
  status?: (typeof INVOICE_STATUSES)[number];

  @IsOptional()
  @IsIn(INVOICE_PAYMENT_PLAN_TYPES)
  paymentPlanType?: (typeof INVOICE_PAYMENT_PLAN_TYPES)[number];

  @IsOptional()
  @IsISO8601()
  dueDate?: string;

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  notes?: string;
}

export class UpdateInvoiceDto {
  @IsOptional()
  @IsIn(INVOICE_STATUSES)
  status?: (typeof INVOICE_STATUSES)[number];

  @IsOptional()
  @IsIn(INVOICE_PAYMENT_PLAN_TYPES)
  paymentPlanType?: (typeof INVOICE_PAYMENT_PLAN_TYPES)[number];

  @IsOptional()
  @IsNumber()
  @Min(0.01)
  amount?: number;

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  notes?: string;
}

export class CreateScholarshipDto {
  @IsString()
  @MinLength(1)
  studentId!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(255)
  title!: string;

  @ValidateIf((o) => o.percent == null)
  @IsNumber()
  @Min(0.01)
  amount?: number;

  @ValidateIf((o) => o.amount == null)
  @IsNumber()
  @Min(0.01)
  @Max(100)
  percent?: number;

  @IsIn(FEE_TERMS)
  term!: (typeof FEE_TERMS)[number];

  @IsOptional()
  @IsString()
  @MaxLength(64)
  sessionLabel?: string;

  @IsOptional()
  @IsIn(SCHOLARSHIP_STATUSES)
  status?: (typeof SCHOLARSHIP_STATUSES)[number];

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  notes?: string;
}

export class UpdateScholarshipDto {
  @IsOptional()
  @IsString()
  @MinLength(1)
  @MaxLength(255)
  title?: string;

  @IsOptional()
  @IsNumber()
  @Min(0.01)
  amount?: number | null;

  @IsOptional()
  @IsNumber()
  @Min(0.01)
  @Max(100)
  percent?: number | null;

  @IsOptional()
  @IsIn(FEE_TERMS)
  term?: (typeof FEE_TERMS)[number];

  @IsOptional()
  @IsString()
  @MaxLength(64)
  sessionLabel?: string | null;

  @IsOptional()
  @IsIn(SCHOLARSHIP_STATUSES)
  status?: (typeof SCHOLARSHIP_STATUSES)[number];

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  notes?: string | null;
}

export class CreateRefundDto {
  @IsOptional()
  @IsString()
  paymentId?: string;

  @IsString()
  @MinLength(1)
  studentId!: string;

  @IsNumber()
  @Min(0.01)
  amount!: number;

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  reason?: string;

  @IsIn(REFUND_METHODS)
  method!: (typeof REFUND_METHODS)[number];

  @IsOptional()
  @IsIn(REFUND_STATUSES)
  status?: (typeof REFUND_STATUSES)[number];

  @IsISO8601()
  date!: string;
}
