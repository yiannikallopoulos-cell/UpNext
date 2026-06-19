/**
 * Discovery view — minimal version.
 *
 * Three simple queries stitched in TypeScript rather than one PostgREST join.
 * The join syntax is fragile and error messages are opaque; this approach is
 * boring but each query is straightforward and each failure mode is obvious.
 *
 * This iteration proves:
 *   1. Next.js connects to Supabase
 *   2. The data renders
 *
 * Once verified, we'll layer in the Bloomberg-meets-Apple aesthetic and
 * the card grid + filters.
 */

import { supabase } from "@/lib/supabase";
import type { CreatorCard, PatternLabel } from "@/lib/types";

async function loadCreators(): Promise<CreatorCard[]> {
  // 1. Load active and graduated creators.
  const { data: creators, error: creatorsError } = await supabase
    .from("creators")
    .select(
      "id, handle, display_name, current_category, current_sub_archetype, lifecycle, scout_brief_short"
    )
    .in("lifecycle", ["active", "graduated"])
    .order("id", { ascending: true });

  if (creatorsError) {
    throw new Error(`Failed to load creators: ${creatorsError.message}`);
  }
  if (!creators || creators.length === 0) {
    return [];
  }

  const creatorIds = creators.map((c) => c.id);

  // 2. Load the latest snapshot per creator (for follower_count).
  // We use a view (v_latest_snapshot) created in the schema for this.
  const { data: snapshots, error: snapshotsError } = await supabase
    .from("v_latest_snapshot")
    .select("creator_id, follower_count")
    .in("creator_id", creatorIds);

  if (snapshotsError) {
    throw new Error(`Failed to load snapshots: ${snapshotsError.message}`);
  }

  // 3. Load the latest score per creator.
  // The scores table is append-only; we want the most recent row per creator.
  // Easiest is to grab all of them and reduce in JS, since we only have ~15 creators.
  const { data: allScores, error: scoresError } = await supabase
    .from("scores")
    .select("creator_id, score, labels, computed_at")
    .in("creator_id", creatorIds)
    .order("computed_at", { ascending: false });

  if (scoresError) {
    throw new Error(`Failed to load scores: ${scoresError.message}`);
  }

  // Build lookup maps for fast joining in TS.
  const followerByCreator = new Map<number, number>();
  for (const s of snapshots ?? []) {
    followerByCreator.set(s.creator_id, s.follower_count);
  }

  // Take the first (most recent) score per creator. Since allScores is sorted
  // descending by computed_at, the first one we see for a given creator is the
  // latest.
  const latestScoreByCreator = new Map<
    number,
    { score: number; labels: PatternLabel[] }
  >();
  for (const s of allScores ?? []) {
    if (!latestScoreByCreator.has(s.creator_id)) {
      latestScoreByCreator.set(s.creator_id, {
        score: s.score,
        labels: (s.labels as PatternLabel[]) ?? [],
      });
    }
  }

  // Stitch it all together.
  return creators.map((c) => ({
    id: c.id,
    handle: c.handle,
    display_name: c.display_name,
    current_category: c.current_category,
    current_sub_archetype: c.current_sub_archetype,
    lifecycle: c.lifecycle,
    follower_count: followerByCreator.get(c.id) ?? 0,
    scout_brief_short: c.scout_brief_short,
    score: latestScoreByCreator.get(c.id)?.score ?? null,
    labels: latestScoreByCreator.get(c.id)?.labels ?? null,
  }));
}

export default async function HomePage() {
  const creators = await loadCreators();

  return (
    <main className="min-h-screen bg-neutral-50 p-8">
      <h1 className="mb-2 text-2xl font-semibold text-neutral-900">
        UpNext — Creator Discovery
      </h1>
      <p className="mb-6 text-sm text-neutral-600">
        {creators.length} creators tracked
      </p>

      <div className="space-y-3">
        {creators.map((creator) => (
          <div
            key={creator.id}
            className="rounded-md border border-neutral-200 bg-white p-4"
          >
            <div className="flex items-baseline justify-between">
              <div>
                <span className="text-base font-medium text-neutral-900">
                  @{creator.handle}
                </span>
                <span className="ml-2 text-xs text-neutral-500">
                  {creator.current_category} ·{" "}
                  {creator.current_sub_archetype ?? "no archetype"}
                </span>
              </div>
              <div className="text-sm text-neutral-700">
                {creator.follower_count.toLocaleString()} followers
              </div>
            </div>
            {creator.scout_brief_short && (
              <p className="mt-2 text-sm text-neutral-700">
                {creator.scout_brief_short}
              </p>
            )}
            <div className="mt-2 text-xs text-neutral-500">
              Score: {creator.score?.toFixed(1) ?? "—"} · Labels:{" "}
              {creator.labels?.join(", ") ?? "none"}
            </div>
          </div>
        ))}
      </div>
    </main>
  );
}
