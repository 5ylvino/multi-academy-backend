/** Great-circle distance between two coordinates in meters. */
export function distanceMeters(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 6371000 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export type VerificationLocation = {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  radiusMeters: number;
};

/** Returns the first configured location that contains the coordinates. */
export function matchVerificationLocation(
  locations: VerificationLocation[],
  latitude: number,
  longitude: number,
): VerificationLocation | null {
  for (const loc of locations) {
    const dist = distanceMeters(latitude, longitude, loc.latitude, loc.longitude);
    if (dist <= loc.radiusMeters) return loc;
  }
  return null;
}
