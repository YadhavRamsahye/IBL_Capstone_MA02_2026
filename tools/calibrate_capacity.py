"""
tools/calibrate_capacity.py
Derive per-camera capacity from recorded snapshots.

detection/severity.py expresses severity as PCU / capacity so that cameras with
different fields of view are comparable. The capacity values shipped there are
informed estimates from the stream geometry, not measurements - this script
replaces them with numbers derived from what each camera actually observed.

Method
------
Capacity is taken as the 95th percentile of observed PCU load per camera. The
reasoning: over a representative period a camera does reach its practical
capacity during peak, but the very top of the distribution is contaminated by
detection noise, so the 95th percentile is the robust choice over the maximum.

    python tools/calibrate_capacity.py --days 7

Needs snapshots in the database. Run it after the detector has been collecting
for at least a few days including peak periods, then paste the output into
CAMERA_CAPACITY in detection/severity.py.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text            # noqa: E402
from database import DB_AVAILABLE, AsyncSessionLocal as _AsyncSession  # noqa: E402


async def calibrate(days: int, percentile: float) -> None:
    if not DB_AVAILABLE or _AsyncSession is None:
        sys.exit("No database connection - cannot calibrate without recorded snapshots.")

    async with _AsyncSession() as db:
        rows = (await db.execute(text("""
            SELECT camera_id,
                   COUNT(*)                                              AS samples,
                   PERCENTILE_CONT(:p) WITHIN GROUP (ORDER BY pcu)::float AS p_pcu,
                   MAX(pcu)::float                                        AS max_pcu,
                   AVG(pcu)::float                                        AS mean_pcu
              FROM traffic_snapshots
             WHERE snapshot_time >= NOW() - make_interval(days => :d)
               AND pcu IS NOT NULL
             GROUP BY camera_id
             ORDER BY camera_id
        """), {"p": percentile, "d": days})).mappings().all()

    if not rows:
        sys.exit(f"No snapshots with PCU data in the last {days} days.")

    print(f"Calibration window: {days} days, percentile: {percentile:.2f}\n")
    print(f"{'camera':<18}{'samples':>9}{'mean':>9}{'p95':>9}{'max':>9}")
    print("-" * 54)
    for r in rows:
        print(f"{r['camera_id']:<18}{r['samples']:>9}{r['mean_pcu']:>9.1f}"
              f"{r['p_pcu']:>9.1f}{r['max_pcu']:>9.1f}")

    thin = [r for r in rows if r["samples"] < 500]
    print("\nPaste into CAMERA_CAPACITY in detection/severity.py:\n")
    print("CAMERA_CAPACITY: dict[str, float] = {")
    for r in rows:
        note = "   # THIN DATA - collect more before trusting" if r in thin else ""
        print(f'    "{r["camera_id"]}": {r["p_pcu"]:.1f},{note}')
    print("}")

    if thin:
        print(f"\n{len(thin)} camera(s) have under 500 samples. Capacity derived "
              "from a short window will track whatever conditions happened to "
              "occur, so collect across several peaks before relying on it.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--percentile", type=float, default=0.95)
    args = ap.parse_args()
    asyncio.run(calibrate(args.days, args.percentile))


if __name__ == "__main__":
    main()
