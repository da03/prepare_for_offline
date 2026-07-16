import type { Trip, TripCoverage, TripSource } from "./api";

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

function firstString(...values: unknown[]): string {
  return values.find((value): value is string => typeof value === "string" && value.trim() !== "")
    ?.trim() ?? "";
}

export function tripId(trip: Trip | null | undefined): string {
  if (!trip) return "";
  return firstString(trip.trip_id, trip.id, trip.context_id);
}

export function tripEvent(trip: Trip | null | undefined): string {
  if (!trip) return "";
  const event = record(trip.event);
  return firstString(trip.event, event.name, event.title, trip.name, trip.title);
}

export function tripDestination(trip: Trip | null | undefined): string {
  if (!trip) return "";
  const destination = record(trip.destination);
  const city = firstString(destination.city);
  const country = firstString(destination.country);
  return firstString(
    trip.destination,
    destination.label,
    [city, country].filter(Boolean).join(", "),
    typeof trip.goal === "string" ? trip.goal : "",
  );
}

export function tripStart(trip: Trip | null | undefined): string {
  return firstString(
    trip?.dates?.start,
    trip?.start_date,
    trip?.starts_at,
  );
}

export function tripEnd(trip: Trip | null | undefined): string {
  return firstString(trip?.dates?.end, trip?.end_date, trip?.ends_at);
}

export function tripNeeds(trip: Trip | null | undefined): string[] {
  if (!trip) return [];
  return cleanStringList(trip.needs?.length ? trip.needs : trip.expected_needs);
}

export function cleanStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (typeof item === "string") return item.trim();
      const itemRecord = record(item);
      return firstString(
        itemRecord.label,
        itemRecord.name,
        itemRecord.title,
        itemRecord.publisher,
      );
    })
    .filter((item): item is string => Boolean(item));
}

export function isTripReady(trip: Trip | null | undefined): boolean {
  if (!trip) return false;
  const status = firstString(trip.status).toLowerCase().replace(/[\s-]+/g, "_");
  return Boolean(
    trip.ready_offline ||
      trip.active_pack_id ||
      status === "ready" ||
      status === "ready_offline",
  );
}

export function tripStatusLabel(trip: Trip | null | undefined): string {
  if (!trip) return "No trip selected";
  if (isTripReady(trip)) return "Ready Offline";
  const status = firstString(trip.status).toLowerCase();
  if (/(prepar|download|index|process|discover)/.test(status)) return "Preparing";
  if (status === "failed") return "Needs attention";
  return "Not prepared";
}

export function tripLabel(trip: Trip | null | undefined): string {
  const event = tripEvent(trip);
  const destination = tripDestination(trip);
  if (event && destination && !event.toLowerCase().includes(destination.toLowerCase())) {
    return `${event} · ${destination}`;
  }
  return event || destination || "Untitled trip";
}

export function mergeCoverage(
  current: TripCoverage | null | undefined,
  next: TripCoverage | null | undefined,
): TripCoverage | null {
  if (!current && !next) return null;
  return { ...(current ?? {}), ...(next ?? {}) };
}

export function coverageSources(
  coverage: TripCoverage | null | undefined,
  trip?: Trip | null,
): TripSource[] {
  const value = coverage?.sources ?? trip?.sources ?? [];
  return Array.isArray(value) ? value : [];
}

export function sourceId(source: TripSource): string {
  return firstString(source.source_id, source.id);
}

export function sourcePublisher(source: TripSource): string {
  return firstString(source.publisher, source.title, "Prepared source");
}

export function isoDateInput(value: string): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value.slice(0, 10);
  const year = parsed.getFullYear();
  const month = String(parsed.getMonth() + 1).padStart(2, "0");
  const day = String(parsed.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
