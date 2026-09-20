import { IsEmail, IsOptional, IsString, MinLength } from 'class-validator';

export class PasswordResetRequestDto {
  @IsEmail()
  email!: string;

  @IsOptional()
  @IsString()
  schoolSlug?: string;
}

export class PasswordResetConfirmDto {
  @IsString()
  @MinLength(10)
  token!: string;

  @IsString()
  @MinLength(8)
  password!: string;
}
