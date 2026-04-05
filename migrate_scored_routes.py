"""One-time migration: rename scored_routes fields from snake_case to camelCase.

Usage:
    python3 migrate_scored_routes.py           # dry run (default)
    python3 migrate_scored_routes.py --apply   # apply changes to Firestore

This aligns scored_routes field names with the routes collection (camelCase).
"""

from google.cloud import firestore

db = firestore.Client(project="hilliness-analyzer")

FIELD_MAP = {
    "score_class": "scoreClass",
    "density_score": "densityScore",
    "intensity_score": "intensityScore",
    "continuity_score": "continuityScore",
    "total_dist_km": "totalDist",
    "total_gain": "totalGain",
    "total_loss": "totalLoss",
    "min_ele": "minEle",
    "max_ele": "maxEle",
    "gain_per_km": "gainPerKm",
    "climb_dist": "climbDist",
    "band_colors": "bandColors",
}


def migrate(dry_run=True):
    collection = db.collection("scored_routes")
    docs = list(collection.stream())
    print(f"Found {len(docs)} scored_routes documents")

    migrated = 0
    skipped = 0
    batch = db.batch()
    batch_count = 0

    for doc in docs:
        data = doc.to_dict()
        updates = {}
        deletes = {}

        for old_key, new_key in FIELD_MAP.items():
            if old_key in data and new_key not in data:
                updates[new_key] = data[old_key]
                deletes[old_key] = firestore.DELETE_FIELD

        if not updates:
            skipped += 1
            continue

        migrated += 1
        if dry_run:
            print(f"  [DRY RUN] {doc.id}: {list(updates.keys())}")
        else:
            combined = {**updates, **deletes}
            batch.update(doc.reference, combined)
            batch_count += 1

            # Firestore batch limit is 500 operations
            if batch_count >= 500:
                batch.commit()
                print(f"  Committed batch of {batch_count}")
                batch = db.batch()
                batch_count = 0

    # Commit remaining
    if not dry_run and batch_count > 0:
        batch.commit()
        print(f"  Committed final batch of {batch_count}")

    print(f"\nDone: {migrated} migrated, {skipped} already up-to-date")


if __name__ == "__main__":
    import sys
    apply = "--apply" in sys.argv
    if apply:
        print("APPLYING migration to Firestore...")
    else:
        print("DRY RUN (pass --apply to execute)")
    migrate(dry_run=not apply)
