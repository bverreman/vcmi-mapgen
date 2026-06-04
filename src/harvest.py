"""Harvest the generation palette. Decoration VOCABULARY and PROPORTIONS come from the
159 HUMAN maps (not the HotA RMG); only the editor-valid object TEMPLATES are borrowed
from the RMG vmaps for the standard types that exist in both.

out/palette.json:
  fill[terrcode]          -> representative flat tile string
  decor_by_terr[terrint]  -> [{type, subtype, template, weight}]  weight = human count on that terrain
  human_decor_density[int]-> decoration objects per 1000 tiles on that terrain (HUMAN target)
"""
import zipfile,re,json,glob,collections,os,sys
sys.path.insert(0,"src"); import ontology as ON, vmaplib, vmapwrite
ROOT="/mnt/data/workspace/vcmi-mapgen"
def relaxed(t): t=re.sub(r'//[^\n]*','',t); t=re.sub(r',(\s*[}\]])',r'\1',t); return json.loads(t)
def camel(s): p=s.lower().split('_'); return p[0]+''.join(w.capitalize() for w in p[1:])

# editor-valid decoration TEMPLATES from RMG, keyed by vmap type
rmg_tpl={}; fill_counts=collections.defaultdict(collections.Counter)
for p in glob.glob("/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/RandomMaps/*.vmap"):
    h,surf,under,objs=vmapwrite.read_raw(p)
    for g in [surf]+([under] if under else []):
        for row in g:
            for c in row:
                if '_ri' not in c and '_ro' not in c: fill_counts[c[:2]][c]+=1
    for o in objs:
        m=o.get("template",{}).get("mask",[])
        if m and not any('A' in r for r in m) and o.get("type") not in rmg_tpl:
            rmg_tpl[o["type"]]={"type":o["type"],"subtype":o.get("subtype","object"),"template":o.get("template",{})}
fill={code:cnt.most_common(1)[0][0] for code,cnt in fill_counts.items()}

# HUMAN decoration: per (terrain, vmap-type) weight; AFFINITY-thresholded so boundary
# anchors don't leak a type onto a terrain it doesn't belong to (no snow-on-desert).
maps=[json.load(open(f)) for f in glob.glob(f"{ROOT}/out/maps/*.json")]
vt_terr=collections.defaultdict(collections.Counter)  # vtype -> Counter(terrint)
tiles=collections.Counter(); dec=collections.Counter()
for m in maps:
    for lvl in m["terrain"]:
        for row in lvl:
            for cell in row: tiles[cell["t"]]+=1
    for o in m["objects"]:
        if ON.resolve(o["class"],o["subclass"])["purpose"]!="DECORATION": continue
        l=o["l"]
        if l>=len(m["terrain"]): continue
        try: t=m["terrain"][l][o["y"]][o["x"]]["t"]
        except: continue
        dec[t]+=1
        vt=camel(ON.name_of(o["class"]))
        if vt in rmg_tpl: vt_terr[vt][t]+=1
decor_by_terr=collections.defaultdict(list)
for vt,cnt in vt_terr.items():
    tot=sum(cnt.values()); top=cnt.most_common(1)[0][0]
    for t,c in cnt.items():
        if c/tot>=0.15 or t==top:                 # affinity gate: real home terrain(s) only
            decor_by_terr[str(t)].append({**rmg_tpl[vt],"weight":c})
decor_by_terr={t:sorted(v,key=lambda d:-d["weight"]) for t,v in decor_by_terr.items()}
human_density={int(t):(1000.0*dec[t]/tiles[t] if tiles[t] else 0) for t in tiles}

json.dump({"fill":fill,"decor_by_terr":decor_by_terr,"human_decor_density":human_density},
          open(f"{ROOT}/out/palette.json","w"))
TN={0:"dirt",1:"sand",2:"grass",3:"snow",4:"swamp",5:"rough",6:"subt",7:"lava",8:"water",9:"rock",10:"highl",11:"waste"}
print("human-sourced decoration per terrain (top types by human weight):")
for t in sorted(decor_by_terr,key=lambda k:int(k)):
    items=decor_by_terr[t]; tot=sum(i["weight"] for i in items)
    top=", ".join(f"{i['type']} {100*i['weight']//tot}%" for i in items[:4])
    print(f"  {TN.get(int(t),t):<7} {top}")
