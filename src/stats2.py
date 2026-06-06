import json, glob, collections, sys

sys.path.insert(0, "/mnt/data/workspace/vcmi-mapgen/src")
import ontology as ON

ROOT = "/mnt/data/workspace/vcmi-mapgen"
maps = [json.load(open(f)) for f in glob.glob(f"{ROOT}/out/maps/*.json")]
NM = len(maps)
tiles = sum(1 for m in maps for lvl in m["terrain"] for row in lvl for _ in row)

purpose = collections.Counter()
res = collections.Counter()
mine = collections.Counter()
fac = collections.Counter()
mono = collections.Counter()
unknown = collections.Counter()
relat = 0
total = 0
for m in maps:
    for o in m["objects"]:
        r = ON.resolve(o["class"], o["subclass"])
        total += 1
        purpose[r["purpose"]] += 1
        if r["relational"]:
            relat += 1
        if r["purpose"] == "UNKNOWN":
            unknown[r["name"]] += 1
        if r["name"] in ("RESOURCE",):
            res[r["subtype"]] += 1
        if r["name"] == "MINE":
            mine[r["subtype"]] += 1
        if r["name"] == "TOWN":
            fac[r["subtype"]] += 1
        if r["name"] == "MONOLITH_TWO_WAY":
            mono[o["subclass"]] += 1

print(f"corpus: {NM} maps, {total} objects, {tiles} tiles, relational objects={relat}")
print("\n=== OBJECT BUDGET BY PURPOSE (the macro 'what is this map made of') ===")
for p, c in purpose.most_common():
    print(f"  {p:<18}{c:>7}  {100 * c / total:>5.1f}%   {c / NM:>6.1f}/map")
print(
    f"\nontology coverage: {100 * (total - sum(unknown.values())) / total:.1f}% of objects have a known purpose"
)
if unknown:
    print("  top UNKNOWN (to author next):", dict(unknown.most_common(8)))

print("\n=== RESOURCE PILES by type (economy) ===")
for k, c in res.most_common():
    print(f"  {k:<10}{c:>5}  {c / NM:>5.1f}/map")
print("=== MINES by type ===")
for k, c in mine.most_common():
    print(f"  {k:<10}{c:>5}  {c / NM:>5.1f}/map")
print("=== TOWNS by faction (fixed towns only) ===")
for k, c in fac.most_common():
    print(f"  {k:<11}{c:>4}")
print("=== TWO-WAY MONOLITH channels (the relational pairing) ===")
for k, c in sorted(mono.items()):
    print(f"  channel {k}: {c} endpoints  ({'OK paired' if c % 2 == 0 else 'ODD - networked >2'})")
