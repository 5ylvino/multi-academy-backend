import { IsString, Matches, MaxLength, ValidateIf } from 'class-validator';

/** Self-service passport photo upload (base64 data URL). */
export class UpdatePassportPhotoDto {
  @ValidateIf((_, v) => v !== null)
  @IsString()
  @MaxLength(280_000, { message: 'Passport photo is too large (max ~200KB encoded)' })
  @Matches(/^data:image\/(jpeg|jpg|png|webp);base64,/, {
    message: 'Passport photo must be a JPEG, PNG, or WebP data URL',
  })
  passportPhoto!: string | null;
}
